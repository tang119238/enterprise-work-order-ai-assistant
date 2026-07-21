package com.tangmeng.workorder.quality;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.tangmeng.workorder.controller.GlobalExceptionHandler;
import com.tangmeng.workorder.security.SecurityConfig;
import com.tangmeng.workorder.security.TenantContext;
import com.tangmeng.workorder.security.TenantContextResolver;
import com.tangmeng.workorder.tenant.TenantTransaction;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.context.annotation.Import;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.security.oauth2.jwt.JwtDecoder;
import org.springframework.security.oauth2.server.resource.authentication.JwtAuthenticationToken;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

import java.sql.ResultSet;
import java.sql.Timestamp;
import java.time.Clock;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.List;
import java.util.Set;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;
import static org.springframework.security.test.web.servlet.request.SecurityMockMvcRequestPostProcessors.authentication;
import static org.springframework.security.test.web.servlet.request.SecurityMockMvcRequestPostProcessors.csrf;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@WebMvcTest(QualityOutboxController.class)
@Import({SecurityConfig.class, GlobalExceptionHandler.class})
class QualityOutboxControllerTest {

    private static final UUID TENANT = UUID.fromString("11111111-1111-1111-1111-111111111111");
    private static final UUID USER = UUID.fromString("00000000-0000-0000-0000-000000009001");
    private static final UUID EVENT = UUID.fromString("00000000-0000-0000-0000-000000009401");
    private static final UUID WORK_ORDER = UUID.fromString("00000000-0000-0000-0000-000000000001");

    @Autowired
    private MockMvc mvc;

    @Autowired
    private ObjectMapper objectMapper;

    @MockitoBean
    private QualityOutboxService service;

    @MockitoBean
    private JwtDecoder jwtDecoder;

    @MockitoBean
    private TenantContextResolver resolver;

    @Test
    void onlyQualityConsumeScopeMayClaimInternalEvents() throws Exception {
        TenantContext wrongScope = context(Set.of("work-order:read"));

        mvc.perform(post("/internal/quality-events/claim")
                .with(authentication(tenantAuthentication(wrongScope)))
                .with(csrf())
                .contentType("application/json")
                .content("{\"limit\":10}"))
            .andExpect(status().isForbidden());

        verify(service, never()).claim(eq(wrongScope), eq(10));
    }

    @Test
    void humanRoleCannotUseServiceScopeToClaimInternalEvents() throws Exception {
        TenantContext human = new TenantContext(
            TENANT, USER, "reviewer", Set.of("QUALITY_REVIEWER"), Set.of(),
            Set.of("quality:consume"), "review-request", "review-trace"
        );

        mvc.perform(post("/internal/quality-events/claim")
                .with(authentication(tenantAuthentication(human)))
                .with(csrf())
                .contentType("application/json")
                .content("{\"limit\":10}"))
            .andExpect(status().isForbidden());

        verify(service, never()).claim(eq(human), eq(10));
    }

    @Test
    void claimPreservesVerifiedTenantAndReturnsBoundedImmutablePayload() throws Exception {
        TenantContext context = context(Set.of("quality:consume"));
        ObjectNode snapshot = objectMapper.createObjectNode()
            .put("id", WORK_ORDER.toString())
            .put("tenant_id", TENANT.toString())
            .put("status", "COMPLETED")
            .put("version", 7);
        ArrayNode attachments = objectMapper.createArrayNode();
        when(service.claim(context, 2)).thenReturn(List.of(
            new QualityOutboxService.ClaimedQualityEvent(
                EVENT, TENANT, WORK_ORDER, 7L, snapshot, attachments, 1, 2,
                LocalDateTime.parse("2026-07-20T01:00:00")
            )
        ));

        mvc.perform(post("/internal/quality-events/claim")
                .with(authentication(tenantAuthentication(context)))
                .with(csrf())
                .contentType("application/json")
                .content("{\"limit\":2}"))
            .andExpect(status().isOk())
            .andExpect(jsonPath("$[0].event_id").value(EVENT.toString()))
            .andExpect(jsonPath("$[0].tenant_id").value(TENANT.toString()))
            .andExpect(jsonPath("$[0].work_order_id").value(WORK_ORDER.toString()))
            .andExpect(jsonPath("$[0].work_order_version").value(7))
            .andExpect(jsonPath("$[0].work_order_snapshot.status").value("COMPLETED"))
            .andExpect(jsonPath("$[0].attachments_summary").isArray())
            .andExpect(jsonPath("$[0].inspection_round").value(1))
            .andExpect(jsonPath("$[0].attempt").value(2));

        verify(service).claim(context, 2);
    }

    @Test
    void rejectsClaimLimitsOutsideOneToFifty() throws Exception {
        TenantContext context = context(Set.of("quality:consume"));

        for (int limit : List.of(0, 51)) {
            mvc.perform(post("/internal/quality-events/claim")
                    .with(authentication(tenantAuthentication(context)))
                    .with(csrf())
                    .contentType("application/json")
                    .content("{\"limit\":" + limit + "}"))
                .andExpect(status().isBadRequest());
        }

        verify(service, never()).claim(eq(context), eq(0));
        verify(service, never()).claim(eq(context), eq(51));
    }

    @Test
    void acknowledgesEventInsideTheSameVerifiedTenant() throws Exception {
        TenantContext context = context(Set.of("quality:consume"));
        when(service.acknowledge(context, EVENT)).thenReturn(true);

        mvc.perform(post("/internal/quality-events/{eventId}/ack", EVENT)
                .with(authentication(tenantAuthentication(context)))
                .with(csrf()))
            .andExpect(status().isNoContent());

        verify(service).acknowledge(context, EVENT);
    }

    @Test
    void claimSqlLeasesRowsAtomicallyAndPreventsConcurrentDoubleClaim() {
        assertThat(QualityOutboxService.CLAIM_SQL).contains(
            "event_type = 'WORK_ORDER_COMPLETED'",
            "status = 'PENDING'",
            "available_at <= ?",
            "FOR UPDATE SKIP LOCKED",
            "attempts = event.attempts + 1",
            "available_at = ?",
            "RETURNING"
        );
        assertThat(QualityOutboxService.LEASE_MINUTES).isEqualTo(5L);
        assertThat(QualityOutboxService.ACK_SQL).contains(
            "tenant_id = ?", "id = ?", "event_type = 'WORK_ORDER_COMPLETED'",
            "status = 'PENDING'"
        );
    }

    @Test
    @SuppressWarnings("unchecked")
    void serviceClaimsInsideTenantTransactionAndWhitelistsSnapshotFields() throws Exception {
        JdbcTemplate jdbc = mock(JdbcTemplate.class);
        TenantTransaction transactions = mock(TenantTransaction.class);
        Clock clock = Clock.fixed(Instant.parse("2026-07-20T01:00:00Z"), ZoneOffset.UTC);
        QualityOutboxService realService = new QualityOutboxService(
            jdbc, transactions, objectMapper, clock
        );
        TenantContext context = context(Set.of("quality:consume"));
        ResultSet result = mock(ResultSet.class);
        when(result.getObject("id", UUID.class)).thenReturn(EVENT);
        when(result.getObject("tenant_id", UUID.class)).thenReturn(TENANT);
        when(result.getObject("aggregate_id", UUID.class)).thenReturn(WORK_ORDER);
        when(result.getString("payload")).thenReturn("""
            {
              "id":"00000000-0000-0000-0000-000000000001",
              "tenant_id":"11111111-1111-1111-1111-111111111111",
              "status":"COMPLETED",
              "version":7,
              "title":"Synthetic completion",
              "attachment_url":"https://forbidden.invalid/private",
              "database_password":"forbidden"
            }
            """);
        when(result.getInt("attempts")).thenReturn(3);
        when(result.getTimestamp("occurred_at"))
            .thenReturn(Timestamp.valueOf("2026-07-20 00:59:00"));
        when(transactions.required(eq(context), any())).thenAnswer(invocation ->
            ((java.util.function.Supplier<?>) invocation.getArgument(1)).get()
        );
        when(jdbc.query(
            eq(QualityOutboxService.CLAIM_SQL),
            any(RowMapper.class),
            any(Object[].class)
        )).thenAnswer(invocation -> {
            RowMapper<QualityOutboxService.ClaimedQualityEvent> mapper = invocation.getArgument(1);
            return List.of(mapper.mapRow(result, 0));
        });

        List<QualityOutboxService.ClaimedQualityEvent> claimed = realService.claim(context, 4);

        assertThat(claimed).hasSize(1);
        assertThat(claimed.get(0).tenantId()).isEqualTo(TENANT);
        assertThat(claimed.get(0).workOrderVersion()).isEqualTo(7L);
        assertThat(claimed.get(0).attempt()).isEqualTo(3);
        assertThat(claimed.get(0).workOrderSnapshot().has("title")).isTrue();
        assertThat(claimed.get(0).workOrderSnapshot().has("attachment_url")).isFalse();
        assertThat(claimed.get(0).workOrderSnapshot().has("database_password")).isFalse();
        verify(transactions).required(eq(context), any());
        verify(jdbc).query(
            eq(QualityOutboxService.CLAIM_SQL),
            any(RowMapper.class),
            eq(TENANT),
            eq(LocalDateTime.parse("2026-07-20T01:00:00")),
            eq(4),
            eq(LocalDateTime.parse("2026-07-20T01:05:00")),
            eq(TENANT)
        );
    }

    private static TenantContext context(Set<String> scopes) {
        return new TenantContext(
            TENANT,
            USER,
            "quality-service",
            Set.of("AI_SERVICE"),
            Set.of(),
            scopes,
            "quality-request",
            "quality-trace"
        );
    }

    private static JwtAuthenticationToken tenantAuthentication(TenantContext context) {
        Jwt jwt = Jwt.withTokenValue("quality-service-token")
            .header("alg", "none")
            .claim("sub", context.subject())
            .build();
        List<SimpleGrantedAuthority> authorities = new ArrayList<>();
        context.roles().stream().map(SimpleGrantedAuthority::new).forEach(authorities::add);
        context.scopes().stream()
            .map(scope -> new SimpleGrantedAuthority("SCOPE_" + scope))
            .forEach(authorities::add);
        JwtAuthenticationToken authentication = new JwtAuthenticationToken(
            jwt, authorities, context.subject()
        );
        authentication.setDetails(context);
        return authentication;
    }
}
