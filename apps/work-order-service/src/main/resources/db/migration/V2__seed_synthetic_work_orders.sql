WITH synthetic AS (
    SELECT
        n,
        'WO-20260718-' || LPAD(n::TEXT, 3, '0') AS work_order_no,
        CASE (n - 1) % 5
            WHEN 0 THEN '公共区域照明巡检异常'
            WHEN 1 THEN '空调机房温度告警'
            WHEN 2 THEN '地下车库排水检查'
            WHEN 3 THEN '消防通道占用处理'
            ELSE '电梯前室保洁复查'
        END || ' #' || n AS title,
        '合成演示工单：用于验证只读查询、返工链路和 AI 助手工具调用，不对应任何真实项目。' AS description,
        (ARRAY['星河中心', '云帆园区', '海棠公寓'])[((n - 1) % 3) + 1] AS project_name,
        (ARRAY['A座/12层/公共走廊', 'B区/设备层/空调机房', '地下二层/车库/东区'])[((n - 1) % 3) + 1] AS space_path,
        CASE
            WHEN n = ANY(ARRAY[8, 18, 28, 38, 48]) THEN 'REWORK'
            WHEN n % 7 = 0 THEN 'EMERGENCY'
            ELSE 'STANDARD'
        END AS order_type,
        CASE
            WHEN n % 10 = 0 THEN 'URGENT'
            WHEN n % 3 = 0 THEN 'HIGH'
            WHEN n % 3 = 1 THEN 'MEDIUM'
            ELSE 'LOW'
        END AS priority,
        (ARRAY['PENDING_DISPATCH', 'PENDING_ACCEPTANCE', 'PROCESSING', 'COMPLETED', 'CLOSED'])[((n - 1) % 5) + 1] AS status,
        (ARRAY['林晓', '周明', '陈安', '赵宁', '许晨'])[((n - 1) % 5) + 1] AS assignee_name,
        (ARRAY['PLAN', 'MANUAL', 'INSPECTION'])[((n - 1) % 3) + 1] AS source,
        CASE
            WHEN n = ANY(ARRAY[8, 18, 28, 38, 48])
                THEN 'WO-20260718-' || LPAD((n - 1)::TEXT, 3, '0')
            ELSE NULL
        END AS root_work_order_no,
        CASE
            WHEN n = ANY(ARRAY[8, 18, 28, 38, 48])
                THEN '首次处理结果未满足合成验收标准，需重新处理并保留根工单关联。'
            ELSE NULL
        END AS rework_reason,
        TIMESTAMP '2026-07-18 08:00:00' + n * INTERVAL '20 minutes' AS created_at
    FROM generate_series(1, 50) AS series(n)
)
INSERT INTO work_order (
    work_order_no,
    title,
    description,
    project_name,
    space_path,
    order_type,
    priority,
    status,
    assignee_name,
    source,
    root_work_order_no,
    rework_reason,
    created_at,
    due_at,
    completed_at
)
SELECT
    work_order_no,
    title,
    description,
    project_name,
    space_path,
    order_type,
    priority,
    status,
    assignee_name,
    source,
    root_work_order_no,
    rework_reason,
    created_at,
    created_at + CASE priority
        WHEN 'URGENT' THEN INTERVAL '2 hours'
        WHEN 'HIGH' THEN INTERVAL '8 hours'
        WHEN 'MEDIUM' THEN INTERVAL '24 hours'
        ELSE INTERVAL '48 hours'
    END AS due_at,
    CASE
        WHEN status IN ('COMPLETED', 'CLOSED') THEN created_at + INTERVAL '90 minutes'
        ELSE NULL
    END AS completed_at
FROM synthetic
ORDER BY n
ON CONFLICT (work_order_no) DO NOTHING;

