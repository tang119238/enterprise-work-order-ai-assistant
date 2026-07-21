package com.tangmeng.workorder.tenant;

import com.tangmeng.workorder.security.TenantContext;
import com.tangmeng.workorder.security.TenantContextResolver;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.stereotype.Service;

import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;
import java.util.UUID;

@Service
public class TenantAccessService {

    private final JdbcTemplate jdbc;
    private final TenantTransaction transactions;
    private final String issuer;

    public TenantAccessService(
        JdbcTemplate jdbc,
        TenantTransaction transactions,
        @Value("${security.jwt.issuer-uri}") String issuer
    ) {
        this.jdbc = jdbc;
        this.transactions = transactions;
        this.issuer = issuer;
    }

    public TenantContext resolve(Jwt jwt) {
        return new TenantContextResolver(this).resolve(jwt);
    }

    public UUID loadCurrentUserId(UUID tenantId, String subject) {
        return transactions.required(tenantId, () -> {
            List<UUID> userIds = jdbc.query("""
                select id
                from user_identity
                where issuer = ? and subject = ? and status = 'ACTIVE'
                """, (result, rowNumber) -> result.getObject("id", UUID.class), issuer, subject);
            return userIds.size() == 1 ? userIds.get(0) : null;
        });
    }

    public Set<String> loadCurrentRoles(UUID tenantId, String subject) {
        return transactions.required(tenantId, () -> new LinkedHashSet<>(jdbc.query("""
            select tm.role
            from tenant_membership tm
            join user_identity ui on ui.id = tm.user_identity_id
            join tenant t on t.id = tm.tenant_id
            where tm.tenant_id = ?
              and ui.issuer = ? and ui.subject = ?
              and ui.status = 'ACTIVE' and t.status = 'ACTIVE' and tm.status = 'ACTIVE'
            order by tm.role
            """, (result, rowNumber) -> result.getString("role"), tenantId, issuer, subject)));
    }

    public Set<UUID> loadCurrentProjects(UUID tenantId, String subject) {
        return transactions.required(tenantId, () -> new LinkedHashSet<>(jdbc.query("""
            select ps.project_id
            from project_scope ps
            join user_identity ui on ui.id = ps.user_identity_id
            join tenant t on t.id = ps.tenant_id
            join project p on p.tenant_id = ps.tenant_id and p.id = ps.project_id
            where ps.tenant_id = ?
              and ui.issuer = ? and ui.subject = ?
              and ui.status = 'ACTIVE' and t.status = 'ACTIVE'
              and ps.status = 'ACTIVE' and p.status = 'ACTIVE'
            order by ps.project_id
            """, (result, rowNumber) -> result.getObject("project_id", UUID.class), tenantId, issuer, subject)));
    }
}
