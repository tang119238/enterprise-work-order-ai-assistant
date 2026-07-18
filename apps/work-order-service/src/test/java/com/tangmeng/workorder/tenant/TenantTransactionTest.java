package com.tangmeng.workorder.tenant;

import com.tangmeng.workorder.security.TenantContext;
import org.junit.jupiter.api.Test;
import org.mockito.InOrder;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.TransactionStatus;

import java.util.Set;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.inOrder;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class TenantTransactionTest {

    private static final UUID TENANT = UUID.fromString("11111111-1111-1111-1111-111111111111");

    @Test
    void setsTransactionLocalTenantBeforeCallingBusinessCode() {
        PlatformTransactionManager transactionManager = mock(PlatformTransactionManager.class);
        TransactionStatus status = mock(TransactionStatus.class);
        JdbcTemplate jdbc = mock(JdbcTemplate.class);
        when(transactionManager.getTransaction(any())).thenReturn(status);
        when(jdbc.queryForObject(
            "select set_config('app.tenant_id', ?, true)", String.class, TENANT.toString()
        )).thenReturn(TENANT.toString());
        TenantContext context = new TenantContext(
            TENANT, UUID.randomUUID(), "dispatcher-1", Set.of("DISPATCHER"), Set.of(), "request", "trace"
        );

        String value = new TenantTransaction(transactionManager, jdbc)
            .required(context, () -> "done");

        assertThat(value).isEqualTo("done");
        InOrder calls = inOrder(transactionManager, jdbc);
        calls.verify(transactionManager).getTransaction(any());
        calls.verify(jdbc).queryForObject(
            "select set_config('app.tenant_id', ?, true)", String.class, TENANT.toString()
        );
        calls.verify(transactionManager).commit(status);
    }
}
