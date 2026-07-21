CREATE UNIQUE INDEX uq_work_order_assignment_open_interval
    ON work_order_assignment(tenant_id, work_order_id)
    WHERE unassigned_at IS NULL;

CREATE OR REPLACE FUNCTION reject_work_order_event_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'work_order_event is append-only';
END;
$$;

CREATE TRIGGER work_order_event_append_only
BEFORE UPDATE OR DELETE ON work_order_event
FOR EACH ROW
EXECUTE FUNCTION reject_work_order_event_mutation();
