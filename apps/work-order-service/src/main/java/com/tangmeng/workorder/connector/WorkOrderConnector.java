package com.tangmeng.workorder.connector;

import com.tangmeng.workorder.security.TenantContext;

import java.util.Optional;
import java.util.UUID;

/**
 * Port interface for work order data access.
 * Implementations may use local PostgreSQL or external HTTP systems.
 */
public interface WorkOrderConnector {

    /**
     * Get a work order by ID.
     */
    ConnectorWorkOrder get(TenantContext context, UUID workOrderId) throws ConnectorException;

    /**
     * Search work orders with criteria and pagination.
     */
    ConnectorPage search(TenantContext context, WorkOrderSearchCriteria criteria, int page, int size) throws ConnectorException;

    /**
     * Create a new work order.
     */
    ConnectorResult create(TenantContext context, CreateWorkOrderCommand command, String idempotencyKey) throws ConnectorException;

    /**
     * Assign a work order to a user.
     */
    ConnectorResult assign(TenantContext context, UUID workOrderId, AssignWorkOrderCommand command, String idempotencyKey) throws ConnectorException;

    /**
     * Update work order fields.
     */
    ConnectorResult update(TenantContext context, UUID workOrderId, UpdateWorkOrderCommand command, long expectedVersion, String idempotencyKey) throws ConnectorException;

    /**
     * Transition work order status.
     */
    ConnectorResult transition(TenantContext context, UUID workOrderId, TransitionWorkOrderCommand command, long expectedVersion, String idempotencyKey) throws ConnectorException;

    /**
     * Find a previous result by idempotency key.
     * Used for reconciliation after UNKNOWN results.
     */
    Optional<ConnectorResult> findByIdempotencyKey(TenantContext context, String operation, String idempotencyKey) throws ConnectorException;
}
