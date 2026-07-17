CREATE TABLE work_order (
    work_order_no VARCHAR(32) PRIMARY KEY,
    title VARCHAR(200) NOT NULL,
    description TEXT NOT NULL,
    project_name VARCHAR(100) NOT NULL,
    space_path VARCHAR(300) NOT NULL,
    order_type VARCHAR(32) NOT NULL,
    priority VARCHAR(32) NOT NULL,
    status VARCHAR(32) NOT NULL,
    assignee_name VARCHAR(64),
    source VARCHAR(32) NOT NULL,
    root_work_order_no VARCHAR(32),
    rework_reason VARCHAR(300),
    created_at TIMESTAMP NOT NULL,
    due_at TIMESTAMP NOT NULL,
    completed_at TIMESTAMP,
    CONSTRAINT fk_work_order_root
        FOREIGN KEY (root_work_order_no) REFERENCES work_order(work_order_no)
);

CREATE INDEX idx_work_order_status ON work_order(status);
CREATE INDEX idx_work_order_priority ON work_order(priority);
CREATE INDEX idx_work_order_project ON work_order(project_name);
CREATE INDEX idx_work_order_assignee ON work_order(assignee_name);
CREATE INDEX idx_work_order_created_at ON work_order(created_at DESC);
CREATE INDEX idx_work_order_root ON work_order(root_work_order_no);
