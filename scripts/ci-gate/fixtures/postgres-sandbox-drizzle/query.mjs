import { drizzle } from "drizzle-orm/node-postgres";
import { sql } from "drizzle-orm";
import pg from "pg";

const client = new pg.Client();
await client.connect();

try {
  const database = drizzle(client);
  const result = await database.execute(sql`
    SELECT
      current_user AS user_name,
      current_database() AS database_name,
      current_setting('server_version') AS server_version,
      inet_server_addr() IS NULL AS unix_socket_only,
      (SELECT count(*) = 0 FROM public.d2_acceptance_marker) AS fresh_database
  `);
  const evidence = result.rows[0];
  if (
    evidence.user_name !== "verjson_ci"
    || evidence.database_name !== process.env.PGDATABASE
    || !String(evidence.server_version).startsWith("17.11")
    || evidence.unix_socket_only !== true
    || evidence.fresh_database !== true
  ) {
    throw new Error(`unexpected Postgres sandbox result: ${JSON.stringify(evidence)}`);
  }

  await database.execute(sql`
    INSERT INTO public.d2_acceptance_marker (marker, state)
    VALUES ('fresh', 'pending')
  `);
  await database.execute(sql`
    UPDATE public.d2_acceptance_marker
    SET state = 'complete', updated_at = '2000-01-01T00:00:00Z'
    WHERE marker = 'fresh'
  `);
  const routineResult = await database.execute(sql`
    SELECT updated_at > '2000-01-01T00:00:00Z'::timestamptz AS routine_ran
    FROM public.d2_acceptance_marker
    WHERE marker = 'fresh'
  `);
  if (routineResult.rows[0].routine_ran !== true) {
    throw new Error("the reviewed Postgres trigger routine did not run");
  }
  console.log(JSON.stringify(evidence));
} finally {
  await client.end();
}
