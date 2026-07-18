package com.tangmeng.workorder.tenant;

import com.tangmeng.workorder.security.TenantContext;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;

import java.util.Objects;
import java.util.UUID;
import java.util.function.Supplier;

@Component
public class TenantTransaction {

    private static final String SET_TENANT_SQL =
        "select set_config('app.tenant_id', ?, true)";

    private final TransactionTemplate transactionTemplate;
    private final JdbcTemplate jdbcTemplate;

    public TenantTransaction(
        PlatformTransactionManager transactionManager,
        JdbcTemplate jdbcTemplate
    ) {
        this.transactionTemplate = new TransactionTemplate(transactionManager);
        this.jdbcTemplate = jdbcTemplate;
    }

    public <T> T required(TenantContext context, Supplier<T> operation) {
        Objects.requireNonNull(context, "context");
        return required(context.tenantId(), operation);
    }

    public <T> T required(UUID tenantId, Supplier<T> operation) {
        Objects.requireNonNull(tenantId, "tenantId");
        Objects.requireNonNull(operation, "operation");
        return transactionTemplate.execute(status -> {
            jdbcTemplate.queryForObject(
                SET_TENANT_SQL,
                String.class,
                tenantId.toString()
            );
            return operation.get();
        });
    }
}
