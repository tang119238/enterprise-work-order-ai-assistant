package com.tangmeng.workorder.api;

import com.fasterxml.jackson.annotation.JsonAnySetter;
import com.fasterxml.jackson.annotation.JsonCreator;
import com.fasterxml.jackson.annotation.JsonProperty;
import com.fasterxml.jackson.databind.JsonNode;
import com.tangmeng.workorder.command.InvalidCommandException;
import com.tangmeng.workorder.command.model.CreateProposalCommand;

public final class ActionProposalRequest {

    private final String actionType;
    private final String targetWorkOrderNo;
    private final JsonNode parameters;

    @JsonCreator
    public ActionProposalRequest(
        @JsonProperty("action_type") String actionType,
        @JsonProperty("target_work_order_no") String targetWorkOrderNo,
        @JsonProperty("parameters") JsonNode parameters
    ) {
        this.actionType = actionType;
        this.targetWorkOrderNo = targetWorkOrderNo;
        this.parameters = parameters;
    }

    public String actionType() {
        return actionType;
    }

    public String targetWorkOrderNo() {
        return targetWorkOrderNo;
    }

    public JsonNode parameters() {
        return parameters;
    }

    public CreateProposalCommand toCommand() {
        return CreateProposalCommand.from(this);
    }

    @JsonAnySetter
    public void rejectUnknownField(String name, JsonNode value) {
        throw new InvalidCommandException();
    }
}
