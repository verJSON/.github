import { index, pgEnum, pgTable, text, timestamp } from "drizzle-orm/pg-core";

export const sandboxState = pgEnum("d2_sandbox_state", ["pending", "complete"]);

export const acceptanceMarker = pgTable(
  "d2_acceptance_marker",
  {
    marker: text("marker").primaryKey(),
    state: sandboxState("state").notNull().default("pending"),
    createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
    updatedAt: timestamp("updated_at", { withTimezone: true }).notNull().defaultNow(),
  },
  (table) => [index("d2_acceptance_marker_state_idx").on(table.state)],
);
