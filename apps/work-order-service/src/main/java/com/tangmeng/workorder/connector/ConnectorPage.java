package com.tangmeng.workorder.connector;

import java.util.List;

/**
 * Paginated work order results.
 */
public record ConnectorPage(
    List<ConnectorWorkOrder> items,
    int totalElements,
    int totalPages,
    int currentPage,
    int pageSize
) {}
