INSERT INTO tenant (id, tenant_key, name, status)
VALUES
    ('11111111-1111-1111-1111-111111111111', 'synthetic-tenant-a', '合成租户 A', 'ACTIVE'),
    ('22222222-2222-2222-2222-222222222222', 'synthetic-tenant-b', '合成租户 B', 'ACTIVE');

SELECT set_config('app.tenant_id', '11111111-1111-1111-1111-111111111111', true);

INSERT INTO project (id, tenant_id, project_key, name, status)
VALUES
    ('00000000-0000-0000-0000-000000010001', '11111111-1111-1111-1111-111111111111', 'xinghe-center', '星河中心', 'ACTIVE'),
    ('00000000-0000-0000-0000-000000010002', '11111111-1111-1111-1111-111111111111', 'yunfan-campus', '云帆园区', 'ACTIVE'),
    ('00000000-0000-0000-0000-000000010003', '11111111-1111-1111-1111-111111111111', 'haitang-apartment', '海棠公寓', 'ACTIVE');

INSERT INTO work_order (
    id, tenant_id, work_order_no, title, description, project_id, project_name, space_path,
    order_type, priority, status, assignee_name, source, root_work_order_no, rework_reason,
    created_at, due_at, completed_at
)
SELECT
    ('00000000-0000-0000-0000-' || lpad(substring(legacy.work_order_no from '[0-9]+$'), 12, '0'))::uuid,
    '11111111-1111-1111-1111-111111111111'::uuid,
    legacy.work_order_no,
    legacy.title,
    legacy.description,
    project.id,
    legacy.project_name,
    legacy.space_path,
    legacy.order_type,
    legacy.priority,
    legacy.status,
    legacy.assignee_name,
    legacy.source,
    legacy.root_work_order_no,
    legacy.rework_reason,
    legacy.created_at,
    legacy.due_at,
    legacy.completed_at
FROM work_order_legacy legacy
JOIN project ON project.tenant_id = '11111111-1111-1111-1111-111111111111'::uuid
    AND project.name = legacy.project_name
WHERE legacy.work_order_no <= 'WO-20260718-025'
ORDER BY legacy.work_order_no;

UPDATE work_order child
SET root_work_order_id = root.id
FROM work_order root
WHERE child.tenant_id = '11111111-1111-1111-1111-111111111111'::uuid
    AND root.tenant_id = child.tenant_id
    AND root.work_order_no = child.root_work_order_no;

SELECT set_config('app.tenant_id', '22222222-2222-2222-2222-222222222222', true);

INSERT INTO project (id, tenant_id, project_key, name, status)
VALUES
    ('00000000-0000-0000-0000-000000020001', '22222222-2222-2222-2222-222222222222', 'xinghe-center', '星河中心', 'ACTIVE'),
    ('00000000-0000-0000-0000-000000020002', '22222222-2222-2222-2222-222222222222', 'yunfan-campus', '云帆园区', 'ACTIVE'),
    ('00000000-0000-0000-0000-000000020003', '22222222-2222-2222-2222-222222222222', 'haitang-apartment', '海棠公寓', 'ACTIVE');

INSERT INTO work_order (
    id, tenant_id, work_order_no, title, description, project_id, project_name, space_path,
    order_type, priority, status, assignee_name, source, root_work_order_no, rework_reason,
    created_at, due_at, completed_at
)
SELECT
    ('00000000-0000-0000-0000-' || lpad(substring(legacy.work_order_no from '[0-9]+$'), 12, '0'))::uuid,
    '22222222-2222-2222-2222-222222222222'::uuid,
    legacy.work_order_no,
    legacy.title,
    legacy.description,
    project.id,
    legacy.project_name,
    legacy.space_path,
    legacy.order_type,
    legacy.priority,
    legacy.status,
    legacy.assignee_name,
    legacy.source,
    legacy.root_work_order_no,
    legacy.rework_reason,
    legacy.created_at,
    legacy.due_at,
    legacy.completed_at
FROM work_order_legacy legacy
JOIN project ON project.tenant_id = '22222222-2222-2222-2222-222222222222'::uuid
    AND project.name = legacy.project_name
WHERE legacy.work_order_no >= 'WO-20260718-026'
ORDER BY legacy.work_order_no;

UPDATE work_order child
SET root_work_order_id = root.id
FROM work_order root
WHERE child.tenant_id = '22222222-2222-2222-2222-222222222222'::uuid
    AND root.tenant_id = child.tenant_id
    AND root.work_order_no = child.root_work_order_no;

DROP TABLE work_order_legacy;
