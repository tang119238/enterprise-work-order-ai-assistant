package com.tangmeng.workorder.connector.local;

import com.tangmeng.workorder.connector.*;
import com.tangmeng.workorder.security.TenantContext;
import com.tangmeng.workorder.service.WorkOrderQueryService;
import org.springframework.stereotype.Component;

import java.util.Optional;
import java.util.UUID;

/**
 * Local PostgreSQL connector implementation.
 * Uses existing domain services for work order operations.
 */
@Component
public class LocalWorkOrderConnector implements WorkOrderConnector {

    private final WorkOrderQueryService queryService;

    public LocalWorkOrderConnector(WorkOrderQueryService queryService) {
        this.queryService = queryService;
    }

    @Override
    public ConnectorWorkOrder get(TenantContext context, UUID workOrderId) throws ConnectorException {
        // Delegate to existing query service
        // This is a simplified implementation
        throw new UnsupportedOperationException("Local connector get not yet implemented");
    }

    @Override
    public ConnectorPage search(TenantContext context, WorkOrderSearchCriteria criteria, int page, int size) throws ConnectorException {
        throw new UnsupportedOperationException("Local connector search not yet implemented");
    }

    @Override
    public ConnectorResult create(TenantContext context, CreateWorkOrderCommand command, String idempotencyKey) throws ConnectorException {
        throw new UnsupportedOperationException("Local connector create not yet implemented");
    }

    @Override
    public ConnectorResult assign(TenantContext context, UUID workOrderId, AssignWorkOrderCommand command, String idempotencyKey) throws ConnectorException {
        throw new UnsupportedOperationException("Local connector assign not yet implemented");
    }

    @Override
    public ConnectorResult update(TenantContext context, UUID workOrderId, UpdateWorkOrderCommand command, long expectedVersion, String idempotencyKey) throws ConnectorException {
        throw new UnsupportedOperationException("Local connector update not yet implemented");
    }

    @Override
    public ConnectorResult transition(TenantContext context, UUID workOrderId, TransitionWorkOrderCommand command, long expectedVersion, String idempotencyKey) throws ConnectorException {
        throw new UnsupportedOperationException("Local connector transition not yet implemented");
    }

    @Override
    public Optional<ConnectorResult> findByIdempotencyKey(TenantContext context, String operation, String idempotencyKey) throws ConnectorException {
        throw new UnsupportedOperationException("Local connector findByIdempotencyKey not yet implemented");
    }
}
