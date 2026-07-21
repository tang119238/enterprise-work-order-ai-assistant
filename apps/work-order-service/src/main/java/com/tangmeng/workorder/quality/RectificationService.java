package com.tangmeng.workorder.quality;

import com.baomidou.mybatisplus.core.toolkit.Wrappers;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.tangmeng.workorder.api.ActionProposalResponse;
import com.tangmeng.workorder.command.ActionNotPermittedException;
import com.tangmeng.workorder.command.ActionProposalService;
import com.tangmeng.workorder.command.IdempotencyConflictException;
import com.tangmeng.workorder.command.InvalidCommandException;
import com.tangmeng.workorder.command.WorkOrderCommandRepository;
import com.tangmeng.workorder.domain.WorkOrderEntity;
import com.tangmeng.workorder.mapper.WorkOrderMapper;
import com.tangmeng.workorder.security.TenantContext;
import com.tangmeng.workorder.service.WorkOrderNotFoundException;
import com.tangmeng.workorder.tenant.TenantTransaction;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Clock;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.Optional;
import java.util.UUID;

@Service
@RequiredArgsConstructor
public class RectificationService {
    static final String CALLBACK_OPERATION = "QUALITY_RESULT_CALLBACK";

    private final RectificationRepository repository;
    private final WorkOrderCommandRepository commandRepository;
    private final WorkOrderMapper workOrderMapper;
    private final ActionProposalService proposalService;
    private final TenantTransaction transactions;
    private final ObjectMapper objectMapper;
    private final Clock clock;

    public QualityResultCallbackResponse accept(
        TenantContext context,
        QualityResultCallback callback,
        String idempotencyKey
    ) {
        if (context == null || callback == null || idempotencyKey == null
            || idempotencyKey.isBlank() || idempotencyKey.strip().length() > 200) {
            throw new InvalidCommandException();
        }
        callback.requireValid();
        if (!context.roles().contains(ActionProposalService.AI_SERVICE)
            || !context.scopes().contains("quality:callback")
            || !context.tenantId().equals(callback.tenantId())) {
            throw new ActionNotPermittedException();
        }
        String key = idempotencyKey.strip();
        String hash = requestHash(callback);
        return transactions.required(context,
            () -> acceptInside(context, callback, key, hash));
    }

    private QualityResultCallbackResponse acceptInside(
        TenantContext context,
        QualityResultCallback callback,
        String key,
        String hash
    ) {
        LocalDateTime now = LocalDateTime.ofInstant(clock.instant(), ZoneOffset.UTC);
        Optional<WorkOrderCommandRepository.StoredIdempotency> stored =
            commandRepository.findIdempotency(context.tenantId(), CALLBACK_OPERATION, key);
        if (stored.isPresent()) {
            return replay(stored.get(), hash);
        }
        if (!commandRepository.reserveIdempotency(
            context.tenantId(), CALLBACK_OPERATION, key, hash, now)) {
            stored = commandRepository.findIdempotency(
                context.tenantId(), CALLBACK_OPERATION, key);
            if (stored.isEmpty()) {
                throw new InvalidCommandException();
            }
            return replay(stored.get(), hash);
        }

        if (!repository.lockWorkOrder(context.tenantId(), callback.workOrderId())) {
            throw new WorkOrderNotFoundException("hidden");
        }
        WorkOrderEntity original = workOrderMapper.selectOne(
            Wrappers.<WorkOrderEntity>lambdaQuery()
                .eq(WorkOrderEntity::getTenantId, context.tenantId())
                .eq(WorkOrderEntity::getId, callback.workOrderId()));
        if (original == null) {
            throw new WorkOrderNotFoundException("hidden");
        }
        if (original.getVersion() != callback.workOrderVersion()
            || !"COMPLETED".equals(original.getStatus())) {
            throw new InvalidCommandException();
        }

        RectificationCaseEntity existing = repository.findByResult(
            context.tenantId(), callback.resultId());
        if (existing != null) {
            return complete(context, key, hash, response(existing));
        }
        RectificationCaseEntity sameRound = repository.findByOriginalRound(
            context.tenantId(), callback.workOrderId(), callback.inspectionRound());
        if (sameRound != null) {
            throw new IdempotencyConflictException();
        }
        if ("SKIP".equals(callback.verdict())) {
            return complete(context, key, hash, new QualityResultCallbackResponse(
                callback.resultId(), null, null, "SKIP", "SKIPPED"
            ));
        }

        ActionProposalResponse proposal = proposalService.createQualityProposal(
            context, original, callback);
        RectificationCaseEntity created = RectificationCaseEntity.builder()
            .id(UUID.randomUUID())
            .tenantId(context.tenantId())
            .originalWorkOrderId(original.getId())
            .currentQualityResultId(callback.resultId())
            .currentVerdict(callback.verdict())
            .proposalId(proposal.id())
            .inspectionRound(callback.inspectionRound())
            .status("PROPOSED")
            .createdBy(context.userId())
            .updatedBy(context.userId())
            .createdAt(now)
            .updatedAt(now)
            .build();
        if (!repository.insertCase(created)) {
            throw new IllegalStateException("Rectification case was not persisted");
        }
        String decision = "PASS".equals(callback.verdict()) ? "CLOSE" : "REQUEST_REWORK";
        if (!repository.insertReviewEvent(
            context.tenantId(), created.getId(), callback.resultId(), decision,
            callback.verdict(), "AI quality result received",
            objectMapper.valueToTree(callback), context.userId(), now)) {
            throw new IllegalStateException("Quality review event was not persisted");
        }
        return complete(context, key, hash, response(created));
    }

    private QualityResultCallbackResponse complete(
        TenantContext context,
        String key,
        String hash,
        QualityResultCallbackResponse response
    ) {
        JsonNode payload = objectMapper.valueToTree(response);
        if (!commandRepository.completeIdempotency(
            context.tenantId(), CALLBACK_OPERATION, key, hash, payload, 200)) {
            throw new IllegalStateException("Quality callback idempotency completion failed");
        }
        return response;
    }

    private QualityResultCallbackResponse response(RectificationCaseEntity entity) {
        String action = "PASS".equals(entity.getCurrentVerdict())
            ? "CLOSE" : "CREATE_RECTIFICATION";
        return new QualityResultCallbackResponse(
            entity.getCurrentQualityResultId(), entity.getId(), entity.getProposalId(),
            action, entity.getStatus()
        );
    }

    private QualityResultCallbackResponse replay(
        WorkOrderCommandRepository.StoredIdempotency stored,
        String hash
    ) {
        if (stored.requestHash() == null || !MessageDigest.isEqual(
            stored.requestHash().getBytes(StandardCharsets.UTF_8),
            hash.getBytes(StandardCharsets.UTF_8))) {
            throw new IdempotencyConflictException();
        }
        if (stored.responsePayload() == null || stored.statusCode() == null
            || stored.statusCode() != 200) {
            throw new InvalidCommandException();
        }
        try {
            return objectMapper.treeToValue(
                stored.responsePayload(), QualityResultCallbackResponse.class);
        } catch (JsonProcessingException | IllegalArgumentException exception) {
            throw new InvalidCommandException(exception);
        }
    }

    private String requestHash(QualityResultCallback callback) {
        try {
            byte[] body = objectMapper.writeValueAsBytes(callback);
            return java.util.HexFormat.of().formatHex(
                MessageDigest.getInstance("SHA-256").digest(body));
        } catch (JsonProcessingException | NoSuchAlgorithmException exception) {
            throw new IllegalStateException(exception);
        }
    }
}
