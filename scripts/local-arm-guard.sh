#!/usr/bin/env bash
readonly ORG_ARM_REPOSITORY_ID=1269388380
readonly ORG_ARM_PATH=.github/workflows/gate-rearm.yml

decide() { # local_arm reason
  printf 'local_arm=%s\n' "$1" >>"$GITHUB_OUTPUT"
  printf 'local arm guard: local_arm=%s: %s\n' "$1" "${2//[[:cntrl:]]/ }"
  exit 0
}

case "$EVENT_NAME/$EVENT_ACTION" in
  pull_request_target/opened|pull_request_target/reopened|pull_request_target/synchronize) ;;
  *) decide true "$EVENT_NAME/$EVENT_ACTION is not a head transition the organization arm receives" ;;
esac

base_ref_path="$(jq -rn --arg r "$BASE_REF" '$r | @uri')"
rules_path="repos/$REPOSITORY/rules/branches/$base_ref_path"
scratch="$(mktemp -d)"
trap 'rm -rf "$scratch"' EXIT
gh api --paginate "$rules_path?per_page=100" </dev/null >"$scratch/rules" 2>"$scratch/error" \
  || decide true "could not read $rules_path: $(head -c 500 "$scratch/error")"
rules="$(jq -s 'add' <"$scratch/rules" 2>/dev/null)" \
  || decide true "the rules response is not JSON"
verdict="$(jq -r --arg path "$ORG_ARM_PATH" --argjson id "$ORG_ARM_REPOSITORY_ID" '
  def readable:
    (.ruleset_id | type) == "number"
    and (.parameters | type) == "object"
    and (.parameters.workflows | type) == "array" and (.parameters.workflows | length) > 0
    and all(.parameters.workflows[]; type == "object" and (.path | type) == "string" and .path != ""
                                     and (.repository_id | type) == "number");
  if type != "array" then "true\tthe rules response is not a JSON array"
  elif any(.[]; type != "object" or (.type == "workflows" and (readable | not))) then
    "true\tthe rules response carries a rule the guard cannot read"
  else [ .[] | select(.type == "workflows") | .parameters.workflows[] | select(.path == $path) ] as $at_path
  | if ($at_path | length) == 0 then "true\tno workflows rule names \($path)"
    elif any($at_path[]; .repository_id != $id) then
      "true\tambiguous: a workflows rule names \($path) from repository \([ $at_path[] | select(.repository_id != $id) | .repository_id ] | map(tostring) | join(", ")), not the organization arm (\($id))"
    else "false\tthe organization arm \($path) from repository \($id) is required on this base ref" end end' <<<"$rules")"
decide "${verdict%%$'\t'*}" "${verdict#*$'\t'}"
