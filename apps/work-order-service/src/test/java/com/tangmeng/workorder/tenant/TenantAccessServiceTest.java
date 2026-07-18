package com.tangmeng.workorder.tenant;

import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.JdbcTemplate;

import java.util.List;
import java.util.Set;
import java.util.UUID;
import java.util.function.Supplier;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class TenantAccessServiceTest {

    private static final UUID TENANT = UUID.fromString("11111111-1111-1111-1111-111111111111");
    private static final UUID USER = UUID.fromString("00000000-0000-0000-0000-000000009001");
    private static final UUID PROJECT = UUID.fromString("00000000-0000-0000-0000-000000010001");

    @Test
    @SuppressWarnings("unchecked")
    void loadsOnlyCurrentIdentityMembershipAndProjectScope() {
        JdbcTemplate jdbc = mock(JdbcTemplate.class);
        TenantTransaction transactions = mock(TenantTransaction.class);
        when(transactions.required(eq(TENANT), any())).thenAnswer(invocation ->
            ((Supplier<?>) invocation.getArgument(1)).get());
        when(jdbc.query(any(String.class), any(org.springframework.jdbc.core.RowMapper.class),
            eq("https://issuer.example"), eq("dispatcher-1")))
            .thenReturn(List.of(USER));
        when(jdbc.query(any(String.class), any(org.springframework.jdbc.core.RowMapper.class),
            eq(TENANT), eq("https://issuer.example"), eq("dispatcher-1")))
            .thenReturn(List.of("DISPATCHER"), List.of(PROJECT));

        TenantAccessService service = new TenantAccessService(
            jdbc, transactions, "https://issuer.example"
        );

        assertThat(service.loadCurrentUserId(TENANT, "dispatcher-1")).isEqualTo(USER);
        assertThat(service.loadCurrentRoles(TENANT, "dispatcher-1")).containsExactly("DISPATCHER");
        assertThat(service.loadCurrentProjects(TENANT, "dispatcher-1")).containsExactly(PROJECT);
    }
}
