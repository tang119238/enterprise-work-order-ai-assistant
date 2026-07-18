package com.tangmeng.workorder.command.model;

import com.fasterxml.jackson.databind.JsonNode;
import com.tangmeng.workorder.api.ActionProposalRequest;
import com.tangmeng.workorder.command.InvalidCommandException;

import java.time.LocalDateTime;
import java.time.format.DateTimeParseException;
import java.util.HashSet;
import java.util.Iterator;
import java.util.Set;
import java.util.UUID;

public sealed interface CreateProposalCommand permits
    CreateProposalCommand.Create,
    CreateProposalCommand.Assign,
    CreateProposalCommand.Update,
    CreateProposalCommand.Accept,
    CreateProposalCommand.Start,
    CreateProposalCommand.Complete,
    CreateProposalCommand.Close,
    CreateProposalCommand.Cancel {

    String actionType();

    String targetWorkOrderNo();

    record Create(
        String workOrderNo,
        String title,
        String description,
        UUID projectId,
        String spacePath,
        String orderType,
        String priority,
        String source,
        LocalDateTime dueAt
    ) implements CreateProposalCommand {
        @Override public String actionType() { return "CREATE"; }
        @Override public String targetWorkOrderNo() { return null; }
    }

    record Assign(
        String targetWorkOrderNo,
        UUID assigneeId,
        String assigneeName,
        String reason
    ) implements CreateProposalCommand {
        @Override public String actionType() { return "ASSIGN"; }
    }

    record Update(
        String targetWorkOrderNo,
        String title,
        String description,
        String priority,
        LocalDateTime dueAt
    ) implements CreateProposalCommand {
        @Override public String actionType() { return "UPDATE"; }
    }

    record Accept(String targetWorkOrderNo) implements CreateProposalCommand {
        @Override public String actionType() { return "ACCEPT"; }
    }

    record Start(String targetWorkOrderNo) implements CreateProposalCommand {
        @Override public String actionType() { return "START"; }
    }

    record Complete(String targetWorkOrderNo) implements CreateProposalCommand {
        @Override public String actionType() { return "COMPLETE"; }
    }

    record Close(String targetWorkOrderNo) implements CreateProposalCommand {
        @Override public String actionType() { return "CLOSE"; }
    }

    record Cancel(String targetWorkOrderNo, String reason) implements CreateProposalCommand {
        @Override public String actionType() { return "CANCEL"; }
    }

    static CreateProposalCommand from(ActionProposalRequest request) {
        if (request == null || request.actionType() == null || request.actionType().isBlank()
            || request.parameters() == null || !request.parameters().isObject()) {
            throw invalid();
        }
        String action = request.actionType().strip();
        JsonNode parameters = request.parameters();
        try {
            return switch (action) {
                case "CREATE" -> create(request.targetWorkOrderNo(), parameters);
                case "ASSIGN" -> assign(target(request), parameters);
                case "UPDATE" -> update(target(request), parameters);
                case "ACCEPT" -> new Accept(targetWithEmptyParameters(request, parameters));
                case "START" -> new Start(targetWithEmptyParameters(request, parameters));
                case "COMPLETE" -> new Complete(targetWithEmptyParameters(request, parameters));
                case "CLOSE" -> new Close(targetWithEmptyParameters(request, parameters));
                case "CANCEL" -> cancel(target(request), parameters);
                default -> throw invalid();
            };
        } catch (InvalidCommandException exception) {
            throw exception;
        } catch (RuntimeException exception) {
            throw new InvalidCommandException(exception);
        }
    }

    private static Create create(String target, JsonNode parameters) {
        if (target != null) {
            throw invalid();
        }
        requireOnly(parameters, "work_order_no", "title", "description", "project_id",
            "space_path", "order_type", "priority", "source", "due_at");
        return new Create(
            requiredText(parameters, "work_order_no"),
            requiredText(parameters, "title"),
            requiredText(parameters, "description"),
            requiredUuid(parameters, "project_id"),
            requiredText(parameters, "space_path"),
            requiredText(parameters, "order_type"),
            requiredText(parameters, "priority"),
            requiredText(parameters, "source"),
            requiredDateTime(parameters, "due_at")
        );
    }

    private static Assign assign(String target, JsonNode parameters) {
        requireOnly(parameters, "assignee_id", "assignee_name", "reason");
        return new Assign(target, requiredUuid(parameters, "assignee_id"),
            requiredText(parameters, "assignee_name"), requiredText(parameters, "reason"));
    }

    private static Update update(String target, JsonNode parameters) {
        requireOnly(parameters, "title", "description", "priority", "due_at");
        String title = optionalText(parameters, "title");
        String description = optionalText(parameters, "description");
        String priority = optionalText(parameters, "priority");
        LocalDateTime dueAt = optionalDateTime(parameters, "due_at");
        if (title == null && description == null && priority == null && dueAt == null) {
            throw invalid();
        }
        return new Update(target, title, description, priority, dueAt);
    }

    private static Cancel cancel(String target, JsonNode parameters) {
        requireOnly(parameters, "reason");
        return new Cancel(target, requiredText(parameters, "reason"));
    }

    private static String targetWithEmptyParameters(ActionProposalRequest request, JsonNode parameters) {
        requireOnly(parameters);
        return target(request);
    }

    private static String target(ActionProposalRequest request) {
        if (request.targetWorkOrderNo() == null || request.targetWorkOrderNo().isBlank()) {
            throw invalid();
        }
        return request.targetWorkOrderNo().strip();
    }

    private static void requireOnly(JsonNode parameters, String... allowedNames) {
        Set<String> allowed = new HashSet<>(Set.of(allowedNames));
        Iterator<String> fields = parameters.fieldNames();
        while (fields.hasNext()) {
            if (!allowed.contains(fields.next())) {
                throw invalid();
            }
        }
    }

    private static String requiredText(JsonNode parameters, String name) {
        String value = optionalText(parameters, name);
        if (value == null) {
            throw invalid();
        }
        return value;
    }

    private static String optionalText(JsonNode parameters, String name) {
        JsonNode value = parameters.get(name);
        if (value == null || value.isNull()) {
            return null;
        }
        if (!value.isTextual() || value.asText().isBlank()) {
            throw invalid();
        }
        return value.asText().strip();
    }

    private static UUID requiredUuid(JsonNode parameters, String name) {
        return UUID.fromString(requiredText(parameters, name));
    }

    private static LocalDateTime requiredDateTime(JsonNode parameters, String name) {
        LocalDateTime value = optionalDateTime(parameters, name);
        if (value == null) {
            throw invalid();
        }
        return value;
    }

    private static LocalDateTime optionalDateTime(JsonNode parameters, String name) {
        String value = optionalText(parameters, name);
        if (value == null) {
            return null;
        }
        try {
            return LocalDateTime.parse(value);
        } catch (DateTimeParseException exception) {
            throw new InvalidCommandException(exception);
        }
    }

    private static InvalidCommandException invalid() {
        return new InvalidCommandException();
    }
}
