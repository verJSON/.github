CREATE TYPE public.d2_sandbox_state AS ENUM ('pending', 'complete');

CREATE TABLE public.d2_acceptance_marker (
  marker text PRIMARY KEY NOT NULL,
  state public.d2_sandbox_state DEFAULT 'pending' NOT NULL,
  created_at timestamp with time zone DEFAULT now() NOT NULL,
  updated_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE INDEX d2_acceptance_marker_state_idx
  ON public.d2_acceptance_marker USING btree (state);

-- BEGIN schema-routines: d2-touch-updated-at
CREATE OR REPLACE FUNCTION public.d2_touch_updated_at()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  NEW.updated_at = clock_timestamp();
  RETURN NEW;
END;
$$;

CREATE TRIGGER d2_acceptance_marker_touch_updated_at
BEFORE UPDATE ON public.d2_acceptance_marker
FOR EACH ROW
EXECUTE FUNCTION public.d2_touch_updated_at();
-- END schema-routines: d2-touch-updated-at
