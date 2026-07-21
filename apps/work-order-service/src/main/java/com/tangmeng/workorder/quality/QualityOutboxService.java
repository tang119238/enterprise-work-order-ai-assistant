package com.tangmeng.workorder.quality;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.tangmeng.workorder.security.TenantContext;
import com.tangmeng.workorder.tenant.TenantTransaction;
import lombok.RequiredArgsConstructor;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.stereotype.Service;

import java.time.Clock;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.List;
import java.util.Set;
import java.util.UUID;

@Service
@RequiredArgsConstructor
public class QualityOutboxService {

    static final long LEASE_MINUTES = 5L;
    static final String CLAIM_SQL = """
        WITH candidate AS (
            SELECT event.id
            FROM outbox_event AS event
            WHERE event.tenant_id = ?
              AND event.aggregate_type = 'WORK_ORDER'
              AND event.event_type = 'WORK_ORDER_COMPLETED'
              AND event.status = 'PENDING'
              AND event.available_at <= ?
            ORDER BY event.available_at, event.occurred_at, event.id
            FOR UPDATE SKIP LOCKED
            LIMIT ?
        ), claimed AS (
            UPDATE outbox_event AS event
            SET attempts = event.attempts + 1,
                available_at = ?
            FROM candidate
            WHERE event.tenant_id = ?
              AND event.id = candidate.id
            RETURNING event.id, event.tenant_id, event.aggregate_id,
                      event.payload::text AS payload, event.attempts,
                      event.occurred_at
        )
        SELECT id, tenant_id, aggregate_id, payload, attempts, occurred_at
        FROM claimed
        ORDER BY occurred_at, id
        """;
    static final String ACK_SQL = """
        UPDATE outbox_event
        SET status = 'PUBLISHED', published_at = ?
        WHERE tenant_id = ?
          AND id = ?
          AND aggregate_type = 'WORK_ORDER'
          AND event_type = 'WORK_ORDER_COMPLETED'
          AND status = 'PENDING'
          AND attempts > 0
        """;

    private static final Set<String> SNAPSHOT_FIELDS = Set.of(
        "id", "tenant_id", "work_order_no", "title", "description", "project_id",
        "project_name", "space_path", "order_type", "priority", "status", "assignee_id",
        "assignee_name", "source", "root_work_order_id", "root_work_order_no",
        "rework_reason", "version", "accepted_at", "created_by", "updated_by",
        "created_at", "due_at", "completed_at", "cancelled_at", "cancel_reason"
    );

    private final JdbcTemplate jdbc;
    private final TenantTransaction transactions;
    private final ObjectMapper objectMapper;
    private final Clock clock;

    public List<ClaimedQualityEvent> claim(TenantContext context, int limit) {
        requireConsumer(context);
        if (limit < 1 || limit > 50) {
            throw new IllegalArgumentException("limit must be between 1 and 50");
        }
        LocalDateTime now = now();
        LocalDateTime leaseUntil = now.plusMinutes(LEASE_MINUTES);
        return transactions.required(context, () -> jdbc.query(
            CLAIM_SQL,
            (result, row) -> mapClaimedEvent(
                result.getObject("id", UUID.class),
                result.getObject("tenant_id", UUID.class),
                result.getObject("aggregate_id", UUID.class),
                result.getString("payload"),
                result.getInt("attempts"),
                result.getTimestamp("occurred_at").toLocalDateTime()
            ),
            context.tenantId(), now, limit, leaseUntil, context.tenantId()
        ));
    }

    public boolean acknowledge(TenantContext context, UUID eventId) {
        requireConsumer(context);
        if (eventId == null) {
            throw new IllegalArgumentException("eventId is required");
        }
        LocalDateTime now = now();
        return transactions.required(context, () -> jdbc.update(
            ACK_SQL, now, context.tenantId(), eventId
        ) == 1);
    }

    private ClaimedQualityEvent mapClaimedEvent(
        UUID eventId,
        UUID tenantId,
        UUID workOrderId,
        String payloadText,
        int attempt,
        LocalDateTime occurredAt
    ) {
        JsonNode payload = readPayload(payloadText);
        requirePayloadIdentity(payload, tenantId, workOrderId);
        long version = payload.path("version").asLong(-1L);
        if (version < 0L || !"COMPLETED".equals(payload.path("status").asText())) {
            throw new IllegalStateException("Invalid completed work-order snapshot");
        }
        int inspectionRound = payload.path("inspection_round").asInt(1);
        if (inspectionRound < 1) {
            throw new IllegalStateException("Invalid inspection round");
        }
        ObjectNode snapshot = objectMapper.createObjectNode();
        SNAPSHOT_FIELDS.forEach(field -> {
            if (payload.has(field)) {
                snapshot.set(field, payload.get(field));
            }
        });
        return new ClaimedQualityEvent(
            eventId,
            tenantId,
            workOrderId,
            version,
            snapshot,
            objectMapper.createArrayNode(),
            inspectionRound,
            attempt,
            occurredAt
        );
    }

    private JsonNode readPayload(String payloadText) {
        try {
            JsonNode payload = objectMapper.readTree(payloadText);
            if (payload == null || !payload.isObject()) {
                throw new IllegalStateException("Invalid outbox payload");
            }
            return payload;
        } catch (JsonProcessingException exception) {
            throw new IllegalStateException("Invalid outbox payload", exception);
        }
    }

    private static void requirePayloadIdentity(JsonNode payload, UUID tenantId, UUID workOrderId) {
        if (!tenantId.toString().equals(payload.path("tenant_id").asText())
            || !workOrderId.toString().equals(payload.path("id").asText())) {
            throw new IllegalStateException("Outbox payload identity mismatch");
        }
    }

    private static void requireConsumer(TenantContext context) {
        if (context == null
            || !context.roles().contains("AI_SERVICE")
            || !context.scopes().contains("quality:consume")) {
            throw new AccessDeniedException("Quality consumer authority is required");
        }
    }

    private LocalDateTime now() {
        return LocalDateTime.ofInstant(clock.instant(), ZoneOffset.UTC);
    }

    public record ClaimedQualityEvent(
        UUID eventId,
        UUID tenantId,
        UUID workOrderId,
        long workOrderVersion,
        JsonNode workOrderSnapshot,
        JsonNode attachmentsSummary,
        int inspectionRound,
        int attempt,
        LocalDateTime occurredAt
    ) {
    }
}
