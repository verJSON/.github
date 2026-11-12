# Netcup stack backup and restore runbook

This runbook covers backup and restore for the Nuremberg development stack described in
[netcup-nuremberg.md](netcup-nuremberg.md): GitLab CE, Nexus CE, MinIO, and the runner
managers. [ADR 0256](../decisions/0256-netcup-stack-backup-and-disaster-recovery/README.md)
decides what is backed up, where it goes, and how often it is drilled. This page holds
the procedures.

Sections 1 to 6 and 8 record the procedures **executed on 2026-11-05** under
[#2032](https://github.com/verJSON/.github/issues/2032): the first offline backup, the
MinIO image recovery, and the restore drill that passed. They include the corrections
from the ARB review. Each step that differs from what ran that day says so.

Section 7, the production restore, has **not been executed**. It is derived from the
drill.

The scheduled job in ADR 0256 is **not built yet**. That covers the host timer, `age`
recipients, and the R2 upload. Until it exists, use sections 1 to 4 to back up by hand.

## Public-repository rules

This repository is public ([README](README.md)), so every command here uses
placeholders. Never commit real values for:

- credentials and passphrases;
- where keys or credentials are stored;
- host access methods, such as SSH aliases or sudo scope;
- PV UUIDs;
- host or workstation paths.

Those details live with the escrowed backup keys (ADR 0256 §7). So does the Nexus
break-glass reset that the drill used.

| Placeholder | Meaning |
| --- | --- |
| `<ns>` | The stack namespace. The generator's default is `verjson-stack`. |
| `<node>` | The k3s node name. |
| `<ts>` | A run's UTC timestamp, in the form `YYYYMMDDTHHMMSSZ`. |
| `<backup-root>` | The root-owned host-local backup directory. It sits outside k3s storage. |
| `<pv-dir:NAME>` | The `local-path` directory of volume `NAME` under `/var/lib/rancher/k3s/storage`. Look it up with `kubectl get pv`. |
| `<helper-image>` | An image already in the node's cache, pinned by digest, with `bash` and GNU `tar`. On 2026-11-05 this was the deployed GitLab CE digest. |
| `<runner-deploys>` | The runner manager Deployments. |
| `<recipients-file>` | The reviewed `age` recipients list. It holds two or more public keys. |
| `<identity-file>` | One key holder's `age` identity. It is never stored on the host. |
| `<keyfile>` | The passphrase file of the one-off 2026-11-05 gpg copy. Key holders hold it; it is never stored on the host. |
| `<offsite-dir>` | The destination of an off-host copy. |
| `<staging-dir>` | A root-owned staging directory on the node for image archives. |

Run every `kubectl` command as `k3s kubectl -n <ns>` on the node, through the access
path recorded with the escrowed keys.

**Run every multi-line block in §2, §4, §6 and §7 as a script.** Save it to a file and
run it with `bash -s < block.sh`, or pipe it the same way over the access path. Never
paste a block into an interactive shell:

- its `exit` would end your session;
- its `set -u` and `pipefail` would stay set in that shell afterwards;
- a failure partway through would leave you at a prompt with nothing reporting it.

## 1. Protect the volumes

Set every stack PV to `Retain` **before** you do anything else. Under reclaim policy
`Delete`, a "delete and recreate" repair destroys the only copy.

```sh
for pv in $(kubectl get pv -o jsonpath='{range .items[?(@.spec.claimRef.namespace=="<ns>")]}{.metadata.name}{"\n"}{end}'); do
  kubectl patch pv "$pv" -p '{"spec":{"persistentVolumeReclaimPolicy":"Retain"}}'
done
kubectl get pv -o custom-columns=CLAIM:.spec.claimRef.name,RECLAIM:.spec.persistentVolumeReclaimPolicy,STATUS:.status.phase
```

**Check:** all five claims show `Retain`: `minio-data`, `nexus-data`, `gitlab-data`,
`gitlab-config` and `gitlab-logs`.

## 2. Quiesce the writers

Record the replica counts first. Then stop GitLab, Nexus **and MinIO**, and wait until
their pods are gone. A running MinIO keeps rewriting files under `.minio.sys` even when
nothing writes to it (ADR 0256 §2). On 2026-11-05 MinIO was already down, so only
GitLab and Nexus were scaled.

The script fails **closed** in each of these cases:

- **Hand-written selector.** It is never trusted. A selector that matches nothing makes
  `kubectl` exit 0 with "No resources found", which looks exactly like a finished
  quiesce. So the script builds each Deployment's selector from its own
  `.spec.selector.matchLabels`.
- **Selector check.** Before scaling, it counts the pods each selector matches, and
  aborts if that is fewer than the replicas the Deployment reports.
- **API errors.** An API error at any point counts as **not quiesced**. A failed
  `kubectl` call is never piped into a count.

```sh
#!/usr/bin/env bash
set -uo pipefail
DEPLOYS=${DEPLOYS:-"gitlab nexus minio"}       # §7 adds <runner-deploys>
die() { echo "$*" >&2; exit 1; }
: > replicas.before; : > selectors
for d in $DEPLOYS; do
  spec=$(kubectl get deploy "$d" -o go-template='{{.spec.replicas}} {{or .status.replicas 0}} {{range $k, $v := .spec.selector.matchLabels}}{{$k}}={{$v}},{{end}}') \
    || die "kube API unavailable or deploy/$d missing: abort before scaling anything"
  read -r want running sel <<< "$spec"; sel=${sel%,}
  [ -n "$sel" ] || die "deploy/$d has no matchLabels selector: abort"
  pods=$(kubectl get pods -l "$sel" -o name) || die "kube API error listing pods of $d: abort"
  matched=$(printf '%s' "$pods" | grep -c .)
  [ "$matched" -ge "$running" ] \
    || die "selector $sel matches $matched pod(s) but deploy/$d reports $running: abort"
  echo "$d=$want" >> replicas.before; echo "$sel" >> selectors
done
kubectl scale $(sed 's/=.*//; s/^/deploy\//' replicas.before) --replicas=0 \
  || die "scale failed: run the restore block below now"
quiesced=no
for i in $(seq 1 60); do
  gone=yes
  while read -r sel; do
    pods=$(kubectl get pods -l "$sel" -o name) || { gone=no; break; }   # API error: not quiesced
    [ -z "$pods" ] || { gone=no; break; }
  done < selectors
  [ "$gone" = yes ] && { quiesced=yes; break; }
  sleep 5
done
echo "quiesced=$quiesced"
[ "$quiesced" = yes ] || die "not quiesced: do not capture; run the restore block below"
```

Restore the recorded counts as soon as the capture ends, whether it succeeded or not:

```sh
while IFS== read -r d r; do kubectl scale "deploy/$d" --replicas="$r"; done < replicas.before
```

## 3. Take the offline capture with a root helper pod

The pod runs as root and mounts the k3s storage directory read-only. It writes to
`<backup-root>`, outside k3s storage. `imagePullPolicy: Never` means a registry outage
cannot block the backup. The output stays **root-owned**: `0700` for directories,
`0600` for files.

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: verjson-offline-backup-<ts-lowercase>
  namespace: <ns>
  labels: {app: verjson-offline-backup}
spec:
  nodeName: <node>
  restartPolicy: Never
  automountServiceAccountToken: false
  containers:
  - name: backup
    image: <helper-image>
    imagePullPolicy: Never
    securityContext: {runAsUser: 0, runAsGroup: 0}
    env: [{name: TS, value: "<ts>"}]
    command: ["/bin/bash", "-c"]
    args:
    - |
      set -euo pipefail
      out=/dst/$TS; mkdir -p "$out"; chmod 700 "$out"; cd /src
      declare -A dirs=(
        [minio-data]=<pv-dir:minio-data>
        [nexus-data]=<pv-dir:nexus-data>
        [gitlab-data]=<pv-dir:gitlab-data>
        [gitlab-config]=<pv-dir:gitlab-config> )
      for n in minio-data nexus-data gitlab-data gitlab-config; do
        d=${dirs[$n]}; test -d "$d"
        printf '%s\t%s\t%s\n' "$n" "$d" "$(du -sb "$d" | cut -f1)" >> "$out/SOURCES.tsv"
        tar --numeric-owner --xattrs --acls --sparse -cf "$out/$n.tar" "$d"
      done
      cd "$out"; sha256sum *.tar > SHA256SUMS; tar --version | head -1 > TOOL
      chmod 600 "$out"/*
      echo BACKUP-OK; cat SOURCES.tsv; cat SHA256SUMS
    volumeMounts:
    - {name: src, mountPath: /src, readOnly: true}
    - {name: dst, mountPath: /dst}
  volumes:
  - {name: src, hostPath: {path: /var/lib/rancher/k3s/storage, type: Directory}}
  - {name: dst, hostPath: {path: <backup-root>, type: DirectoryOrCreate}}
```

**Changed from 2026-11-05:** that run also executed `chown -R 1000:1000` on the output,
so the unprivileged transfer user could read it. That was a one-off. Do not repeat it;
§4 streams the files as root instead.

`gitlab-logs` is deliberately excluded (ADR 0256 §1).

```sh
kubectl apply -f backup-pod.yaml
P=verjson-offline-backup-<ts-lowercase>
for i in $(seq 1 180); do
  ph=$(kubectl get pod "$P" -o jsonpath='{.status.phase}')
  case $ph in Succeeded|Failed) break;; esac; sleep 5
done; echo "phase=$ph"; kubectl logs "$P" | tail -12
kubectl delete pod "$P" --wait=true
# Then restore the recorded replica counts (§2)
```

**Check:**

- `phase=Succeeded`, and the log shows `BACKUP-OK`, `SOURCES.tsv` and `SHA256SUMS`. On
  2026-11-05 tar printed one notice about an ignored runtime socket. That is expected.
- `gitlab` and `nexus` return to `1/1` ready, and the GitLab sign-in page returns 200.
  On 2026-11-05 the downtime was about 2 minutes.

Sizes on 2026-11-05:

| Volume | Bytes |
| --- | --- |
| `minio-data` | 5,748,139,387 |
| `nexus-data` | 199,779,467 |
| `gitlab-data` | 346,842,922 |
| `gitlab-config` | 179,975 |

## 4. Make the encrypted off-host copy and verify it

Run this on the machine that receives the copy. Read each archive from the host as root,
exactly once, and `tee` it into `sha256sum` while the encryptor runs in the foreground.
Then check:

- the plaintext hash equals the host's `SHA256SUMS`;
- the encryptor exited 0;
- every encrypted file decrypts back to that same hash.

Any failure stops the run with exit 1, and `ENCRYPTED-SHA256SUMS` is **only** written
after every check passes.

```sh
#!/usr/bin/env bash
set -uo pipefail
TS=<ts>; OUT=<offsite-dir>/$TS; mkdir -p "$OUT"; fail=0
# host-meta.txt = the host's SHA256SUMS followed by SOURCES.tsv, read as root
grep '\.tar$' host-meta.txt > host-sums.txt
for n in gitlab-config gitlab-data nexus-data minio-data; do
  want=$(awk -v f="$n.tar" '$2==f{print $1}' host-sums.txt)
  rc=0
  { <stream <backup-root>/$TS/$n.tar from the host as root> \
      | tee /dev/fd/3 | age -R <recipients-file> -o "$OUT/$n.tar.age"; } \
    3> >(sha256sum | cut -d' ' -f1 > "$OUT/.$n.plain") || rc=$?
  wait $!                                      # the hasher; see the note below
  if [ "$rc" = 0 ] && [ -n "$want" ] && [ "$(cat "$OUT/.$n.plain")" = "$want" ]; then
    echo "transfer $n OK"
  else
    echo "transfer $n FAILED (rc=$rc)"; fail=$((fail + 1))
  fi
done
for n in gitlab-config gitlab-data nexus-data minio-data; do
  want=$(awk -v f="$n.tar" '$2==f{print $1}' host-sums.txt)
  s=$(age -d -i <identity-file> "$OUT/$n.tar.age" | sha256sum | cut -d' ' -f1) || s=error
  if [ "$s" = "$want" ]; then echo "decrypt-verify $n OK"
  else echo "decrypt-verify $n FAILED"; fail=$((fail + 1)); fi
done
rm -f "$OUT"/.*.plain
if [ "$fail" != 0 ]; then
  echo "$fail check(s) failed; ENCRYPTED-SHA256SUMS not written" >&2; exit 1
fi
cp host-meta.txt "$OUT/HOST-SHA256SUMS-and-SOURCES.txt"
(cd "$OUT" && sha256sum *.tar.age > ENCRYPTED-SHA256SUMS)
```

**Check:** the script exits 0, and every line reads `… OK`.

**Why the hasher hangs off a group redirection.** The hasher runs in a process
substitution attached to the group's redirection (`{ …; } 3> >(…)`), not inside the
pipeline. Inside a pipeline, as in `… | tee >(…) | …`, the process substitution belongs
to `tee`'s subshell. The parent shell then cannot `wait` for it, and under `set -u`,
`$!` is unbound. Attached to the redirection, it is the parent shell's child, so
`wait $!` holds until the hash file is complete. This needs bash 4.4 or later, and §6b
uses the same pattern.

**Changed from 2026-11-05:**

- That run encrypted with symmetric gpg (`--symmetric --cipher-algo AES256
  --passphrase-file <keyfile>`), writing `.tar.gpg` files.
- It started gpg inside the process substitution and slept 2 s before verifying, with
  no failure count. Its decrypt-verify passed for all four archives.
- New copies use `age` to the reviewed recipients (ADR 0256 §3), with the encryptor in
  the foreground.

## 5. Recover a runtime image the node cannot pull

On 2026-11-05 no public registry served MinIO's pinned digest anonymously, but the org's
GHCR copy did. These steps put the exact digest back into the node's containerd. They
also put it into `agent/images`, so k3s re-imports and pins it on every start.

**5a. Pull by digest and keep the manifest byte-identical.** Run this on a workstation:

```sh
REF=ghcr.io/<org-mirror>/minio@sha256:<digest>
crane pull --format=oci "$REF" minio-oci
# Confirm both equal the pin: the registry's Docker-Content-Digest header, and the sha256 of the manifest body
python3 - <<'EOF'
import json
p = 'minio-oci/index.json'; d = json.load(open(p))
assert len(d['manifests']) == 1 and d['manifests'][0]['digest'] == 'sha256:<digest>'
d['manifests'][0].setdefault('annotations', {}).update({
    'io.containerd.image.name': 'quay.io/minio/minio:<tag>',
    'org.opencontainers.image.ref.name': '<tag>'})
json.dump(d, open(p, 'w'), separators=(',', ':'))
EOF
tar --sort=name -C minio-oci -cf minio-<tag>.oci.tar .
sha256sum minio-<tag>.oci.tar | tee minio.oci.tar.sha256
```

Copy the archive to `<staging-dir>` on the node. Also store it with every backup copy
(ADR 0256 §8.4).

**5b. Import the archive and pin it with a short-lived root pod.** Use the same
skeleton as §3: the same node, `imagePullPolicy: Never`, root, and no service-account
token. Mount these hostPaths:

- `/run/k3s/containerd/containerd.sock`, as `Socket`;
- `/usr/local/bin/k3s`, as `File`, read-only;
- `<staging-dir>`, read-only;
- `/var/lib/rancher/k3s/agent/images`, as `DirectoryOrCreate`;
- `<backup-root>`.

The pod's script:

```sh
set -euo pipefail
f=/staged/minio-<tag>.oci.tar
echo "<archive-sha256>  $f" | sha256sum -c -
ctr() { /k3s ctr --address /containerd.sock -n k8s.io "$@"; }
ctr images import --all-platforms "$f"
install -m 0644 "$f" /agent-images/minio-<tag>.oci.tar
install -D -m 0644 "$f" /backups/images/minio-<tag>.oci.tar
ctr images ls | grep -E 'minio' || true
echo IMPORT-OK
```

**5c. Add the digest reference and the CRI `managed` label.** Use a second pod with the
same mounts. The workload references the image **by digest**, so the kubelet looks up
the digest reference, not the tag. The label is what lets the CRI see and manage the
image.

```sh
set -euo pipefail
ctr() { /k3s ctr --address /containerd.sock -n k8s.io "$@"; }
src=quay.io/minio/minio:<tag>
dst=quay.io/minio/minio@sha256:<digest>
ctr images ls -q | grep -qxF "$dst" || ctr images tag --skip-reference-check "$src" "$dst" \
  && ctr images label "$dst" io.cri-containerd.image=managed
ctr images ls | grep minio
echo REF-OK
```

`ctr images tag --label …` does not work for this. Tag with `--skip-reference-check`
first, then add the label as a separate step.

**Check:**

- The deployment is unchanged: the same digest, with `IfNotPresent`.
- It reaches `1/1 Running`, and `/minio/health/live` returns 200. On 2026-11-05 that
  happened at 03:07:57Z.

**5d. Restart dependants that latched a failure.**

- **Nexus:** an S3 blob store that reached `Invalid state: FAILED` stays failed until
  Nexus restarts. Run `kubectl rollout restart deploy/nexus`, then confirm no new
  `Invalid state: FAILED` lines appear.
- **Runner pods:** pods stuck in `Unknown` since the outage are recreated only once
  Nexus serves manifests again. Delete them and let their Deployments recreate them.
  Then confirm GitLab shows both runners `online`.

## 6. Restore drill

Restore an off-host copy into a **disposable** Docker environment, off the production
host, and verify every tier. The 2026-11-05 drill passed this way. While you work:

- label every resource;
- keep the network `--internal`;
- stream plaintext straight into volumes, so it never lands on disk as a file.

Set `DECRYPT` to match the copy:

- for `age` copies (`.tar.age`): `age -d -i <identity-file>`;
- for the 2026-11-05 copy (`.tar.gpg`): `gpg --batch --quiet --passphrase-file <keyfile> -d`.

**6a. Integrity, images and volumes.**

```sh
set -uo pipefail
cd <offsite-dir>/<ts>
sha256sum -c ENCRYPTED-SHA256SUMS || exit 1
sha256sum -c minio.oci.tar.sha256 || exit 1
docker load -i minio-<tag>.oci.tar          # the OCI archive that travels with the copy
docker pull <nexus-image@digest>            # or load the archive kept with the copy
docker pull <gitlab-image@digest>
docker network create --internal --label drill=<issue> drill-net
for v in drill-minio-data drill-nexus-data drill-gitlab-data drill-gitlab-config; do
  docker volume create --label drill=<issue> "$v"
done
```

**6b. Stream-decrypt into the volumes and record the plaintext hashes.** A failure in
any stage fails the restore. Each restore waits for its hash process before the hashes
are compared.

```sh
set -uo pipefail
EXT=<age|gpg>; DECRYPT=(<decrypt command from above>); fail=0
restore() { local name=$1 vol=$2 s rc=0; s=$(date +%s)
  { "${DECRYPT[@]}" "$name.tar.$EXT" | tee /dev/fd/3 \
      | docker run -i --rm --network none -u 0 -v "$vol":/restore --entrypoint tar <nexus-image@digest> \
          --numeric-owner --xattrs --acls -xpf - -C /restore --strip-components=1; } \
    3> >(sha256sum | awk -v n="$name.tar" '{print $1"  "n}' > "plain-$name.sha256") || rc=$?
  wait $!                                      # the hasher (see §4)
  echo "$name rc=$rc seconds=$(( $(date +%s) - s ))"; [ "$rc" = 0 ]; }
restore nexus-data drill-nexus-data       || fail=$((fail + 1))
restore gitlab-config drill-gitlab-config || fail=$((fail + 1))
restore gitlab-data drill-gitlab-data     || fail=$((fail + 1))
restore minio-data drill-minio-data       || fail=$((fail + 1))
cat plain-*.sha256 | sort | diff - <(grep '\.tar$' HOST-SHA256SUMS-and-SOURCES.txt | sort) \
  && echo PLAINTEXT-SHA256-ALL-MATCH || fail=$((fail + 1))
[ "$fail" = 0 ] || { echo "restore failed ($fail)" >&2; exit 1; }
```

`--strip-components=1` drops the PV directory name that §3 stored as each archive's top
level.

**Changed from 2026-11-05:** that run had no `pipefail` and no failure count, and it
used a fixed `sleep 2` instead of `wait`. Its plaintext hashes all matched.

**6c. MinIO.** Start the **pinned digest** with a throwaway root. Any new root can open
an `xl-single` volume.

```sh
docker run -d --name drill-minio --label drill=<issue> --network drill-net --network-alias minio \
  -u 1000:1000 -v drill-minio-data:/data \
  -e MINIO_ROOT_USER=drill$(openssl rand -hex 4) -e MINIO_ROOT_PASSWORD=$(openssl rand -hex 24) \
  quay.io/minio/minio@sha256:<digest> server /data
```

Verify:

- `.minio.sys/format.json` reads `"format":"xl-single"`;
- `mc du` for each `verjson-registry-*` bucket;
- `mc admin user list` and `mc admin policy list`;
- a **full read**: `mc cat` every key that `mc ls -r` lists, with 0 failures.

On 2026-11-05: 8 buckets, 1,546 objects, 0 failures.

**6d. Nexus.** Run the pinned digest on `drill-nexus-data`, on `drill-net`, with the
network alias `nexus`. Wait for `/service/rest/v1/status` to return 200, then sign in
with the escrowed break-glass credential. Verify:

- the `verjson-s3` blob store is listed and available;
- the log has **no** `Invalid state: FAILED`;
- the runner manifest `manager@sha256:<digest>` returns 200, and both its digest header
  and its body hash equal the pin;
- every nested manifest and blob verifies by digest.

On 2026-11-05: 752 blobs, and 44 of 44 verified.

**6e. GitLab, using the fresh-user fallback.** You do not need the production GitLab S3
key. Create a new MinIO user, attach the existing `verjson-gitlab` policy, and point
GitLab's object store at that user:

```sh
P=$(openssl rand -hex 24)
docker exec drill-minio sh -c "HOME=/tmp mc admin user add d gldrill $P && HOME=/tmp mc admin policy attach d verjson-gitlab --user gldrill"
# GITLAB_OMNIBUS_CONFIG: set gitlab_rails['object_store']['connection'] to
#   endpoint http://minio:9000, path_style true, access key gldrill, secret $P,
# and keep the production verjson-registry-* bucket names.
docker run -d --name drill-gitlab --label drill=<issue> --hostname gitlab --network drill-net \
  --shm-size 256m -v drill-gitlab-config:/etc/gitlab -v drill-gitlab-data:/var/opt/gitlab \
  -e GITLAB_OMNIBUS_CONFIG="<config above>" <gitlab-image@digest>
unset P
```

Once GitLab is ready, use `gitlab-rails runner` to check:

- the version, and the project, user and group counts;
- each project's default-branch HEAD;
- that every upload and job artifact is readable.

Then require 0 failures from `gitlab-rake gitlab:doctor:secrets`.

On 2026-11-05: 3 projects, 9 users and 1 group, with matching HEADs. 4 of 4 uploads and
26 of 26 artifacts were readable.

**6f. Record the timings.** Log a UTC timestamp at each step, and compare against the
ADR 0256 §4 baseline. On 2026-11-05:

| Step | Time |
| --- | --- |
| Restore of all four volumes | 245 s |
| MinIO live | 3 s |
| Nexus REST | 24 s |
| GitLab ready | 86 s |

**6g. Tear down and check for leaked secrets.**

```sh
docker rm -f $(docker ps -aq --filter label=drill=<issue>)
docker volume rm $(docker volume ls -q --filter label=drill=<issue>)
docker network rm drill-net
grep -rIilE 'secret_access|secretKey|accessKeyId|password[=:"]|BEGIN .*PRIVATE|MINIO_ROOT' <evidence-dir> || echo no-secret-matches
```

Post a receipt to the tracking issue: counts, timings, pass or fail, and digests. Never
include credentials, endpoints or host inventory.

## 7. Production restore with the host intact

**This procedure has not been executed.** It applies the drill to the live node, for
the case where the host survives but its data is lost or corrupt. Follow it exactly.

1. **Pick one run, and put its four volume tars in `<backup-root>/<ts>/`.** Every
   volume must come from the **same** `<ts>`. Mixing runs breaks the pairing between
   the Nexus database and its blobs, and between GitLab and its object storage.
   - **Host-local copy:** if `<backup-root>/<ts>/` still holds the run, use it as it
     is.
   - **R2 copy:** a key holder does the following off the host:
     1. Download `daily/<ts>/` or `monthly/<ts>/` with the reader credential.
     2. Check the ciphertext lines: `grep '\.age$' SHA256SUMS | sha256sum -c -`.
     3. For each of the four volumes, run `age -d -i <identity-file> <n>.tar.age` and
        stream it **as root, with `umask 077`**, into `<backup-root>/<ts>/<n>.tar` on
        the host.
     4. Copy `SHA256SUMS` alongside the tars. First make sure `<backup-root>` has room
        for the four tars.

   Either way, verify **only the plaintext volume lines**. A run's `SHA256SUMS` also
   lists `.age` files that are not on the host:

   ```sh
   cd <backup-root>/<ts>
   grep -E '  (minio-data|nexus-data|gitlab-data|gitlab-config)\.tar$' SHA256SUMS > PLAIN-SUMS
   [ "$(wc -l < PLAIN-SUMS)" = 4 ] && sha256sum -c PLAIN-SUMS
   ```

   `sha256sum -c --ignore-missing` is not good enough here. A tar that is itself
   missing would pass silently.
2. **Quiesce everything.** Run §2 with `DEPLOYS="gitlab nexus minio <runner-deploys>"`.
   Do not continue unless it ends with `quiesced=yes`.
3. **Move the live data aside, then extract each claim.** Use a root pod built on the §3
   skeleton, but mount `/var/lib/rancher/k3s/storage` **read-write** at `/src` and
   `<backup-root>` read-only at `/backups`. **Never delete the live directories.** The
   pod's script:
   - re-checks the plaintext sums;
   - requires free space in the storage filesystem for the extracted data while the
     moved-aside originals are kept;
   - if any step fails, puts every moved directory back. A partial extraction is kept
     as `*.failed-restore-*` rather than deleted.

   ```sh
   set -euo pipefail
   TS=<ts>; now=$(date -u +%Y%m%dT%H%M%SZ); B=/backups/$TS; cd /src
   grep -E '  (minio-data|nexus-data|gitlab-data|gitlab-config)\.tar$' "$B/SHA256SUMS" > /tmp/plain.sums
   [ "$(wc -l < /tmp/plain.sums)" = 4 ] || { echo "run $TS lacks four volume sums" >&2; exit 1; }
   (cd "$B" && sha256sum -c /tmp/plain.sums)
   need=$(stat -c %s "$B"/{minio-data,nexus-data,gitlab-data,gitlab-config}.tar | awk '{s+=$1} END{print int(s*1.1)}')
   avail=$(df -B1 --output=avail /src | tail -1)
   [ "$avail" -gt "$need" ] || { echo "need $need bytes free in storage, have $avail" >&2; exit 1; }
   declare -A dirs=(        # must match SOURCES.tsv of the chosen run
     [minio-data]=<pv-dir:minio-data>  [nexus-data]=<pv-dir:nexus-data>
     [gitlab-data]=<pv-dir:gitlab-data> [gitlab-config]=<pv-dir:gitlab-config> )
   moved=()
   undo() {
     echo "restore failed: moving the originals back" >&2
     for n in "${moved[@]}"; do
       d=${dirs[$n]}
       if [ -e "$d" ]; then mv "$d" "$d.failed-restore-$now"; fi
       mv "$d.pre-restore-$now" "$d"
     done
   }
   trap undo ERR
   # A deleted or evicted restore pod gets SIGTERM, which ERR does not catch.
   trap 'undo; exit 1' TERM INT
   for n in minio-data nexus-data gitlab-data gitlab-config; do
     d=${dirs[$n]}
     mv "$d" "$d.pre-restore-$now"; moved+=("$n")
     tar --numeric-owner --xattrs --acls -xpf "$B/$n.tar" -C /src
     test -d "$d"; echo "restored $n"
   done
   trap - ERR TERM INT
   echo RESTORE-OK
   ```

   Each archive's top-level directory is the PV directory it came from. If a claim now
   has a different PV directory, create that directory and extract with
   `--strip-components=1 -C /src/<new pv-dir>`.
4. **Start the stack in order,** checking each step before the next:
   1. **Images.** Every pinned digest must be present: check with
      `k3s ctr -n k8s.io images ls -q | grep -xF <ref@digest>`. Import any missing image
      from the archives kept with the copy (§5).
   2. **Manifests and Secrets.** Only if they were lost or changed. A key holder
      decrypts `cluster.tar.age` off the host and extracts each export to stdout. The
      exports are `kubectl get -o json` lists. Strip the server-set fields, then apply:

      ```sh
      age -d -i <identity-file> cluster.tar.age | tar -xOf - <export>.json \
        | jq '.items[] |= del(.metadata.resourceVersion, .metadata.uid, .metadata.creationTimestamp,
              .metadata.generation, .metadata.managedFields, .metadata.selfLink,
              .metadata.annotations["kubectl.kubernetes.io/last-applied-configuration"], .status)' \
        | <kubectl apply -f - on the node>
      ```

      Plaintext never touches the disk.
   3. **MinIO, with root access on.**
      - Run `kubectl set env deploy/minio MINIO_API_ROOT_ACCESS=on` and scale it to its
        recorded count. Then run the §6c checks.
      - If GitLab's S3 key is lost, create the fallback user (§6e).
      - Then run `kubectl set env deploy/minio MINIO_API_ROOT_ACCESS=off`, wait for the
        rollout, and confirm that a root API call is **refused**.
      - Until the generator owns this toggle, the env change is deliberate drift. Remove
        it as soon as you are done.
   4. **Nexus.** Scale it to its recorded count from `replicas.before`, then run the
      §6d checks. If `Invalid state: FAILED` appears, run
      `kubectl rollout restart deploy/nexus`.
   5. **GitLab.** Scale it to its recorded count, wait for readiness, then require 0
      failures from `gitlab:doctor:secrets`.
   6. **Runners.** Scale them to their recorded counts, and confirm GitLab shows them
      `online`.
5. **Keep the `*.pre-restore-*` directories** until the restore has been accepted.
   Step 3's free-space check reserved room for them. Removing them is a separate,
   explicit decision.
6. **Post a receipt** in the format of §6g.

## 8. Storage-engine rehearsal (Silo)

This ran in the 2026-11-05 drill, before any engine migration.

1. Copy the restored MinIO volume with `cp -a`.
2. Prove the copy is byte-identical. Compare a sorted `sha256sum` list of every file,
   then a sorted list of mode, owner and path.
3. Start `pgsty/silo@sha256:<digest>` on the copy, with a throwaway root.
4. Repeat §6c and §6d against Silo, then add a Nexus write probe:
   1. Create a raw hosted repository on `verjson-s3`.
   2. PUT a 1 MiB random file, GET it back and compare.
   3. Delete the repository.
5. Stop Silo, and compare `.minio.sys` file hashes against the copy taken before Silo
   started. On 2026-11-05 only the usage, bloom and metacache caches changed;
   `format.json`, `pool.bin`, `config.json` and IAM stayed byte-identical.
6. Start the **pinned MinIO** on the Silo-touched copy, and repeat the checks. This
   rehearses the rollback.

Silo does not guarantee that rollback works, so the real rollback is the backup taken
before the migration. Take a fresh backup (§2 to §4) immediately before any production
engine change.

## Not yet executed

These parts of ADR 0256 have no executed procedure yet. Add each one to this runbook the
first time it runs:

- the scheduled host timer and its safety requirements (ADR 0256 §3);
- `gitlab-backup create STRATEGY=copy`, and the export of cluster manifests and
  Secrets;
- the R2 upload with `rclone copy --immutable` and `no_check_bucket`;
- the lock probe;
- the third-copy pull;
- the external freshness monitor and its heartbeat;
- the §7 production restore, and a restore after losing the whole host.
