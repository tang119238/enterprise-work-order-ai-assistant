package com.tangmeng.workorder.api;

import com.fasterxml.jackson.annotation.JsonAnySetter;
import com.fasterxml.jackson.annotation.JsonCreator;
import com.fasterxml.jackson.annotation.JsonProperty;
import com.fasterxml.jackson.databind.JsonNode;
import com.tangmeng.workorder.command.InvalidCommandException;

public final class ConfirmProposalRequest {

    private final String decision;

    @JsonCreator
    public ConfirmProposalRequest(@JsonProperty("decision") String decision) {
        if (!"CONFIRM".equals(decision) && !"REJECT".equals(decision)) {
            throw new InvalidCommandException();
        }
        this.decision = decision;
    }

    public String decision() {
        return decision;
    }

    public ConfirmProposalRequest requireConfirm() {
        return require("CONFIRM");
    }

    public ConfirmProposalRequest requireReject() {
        return require("REJECT");
    }

    private ConfirmProposalRequest require(String expected) {
        if (!expected.equals(decision)) {
            throw new InvalidCommandException();
        }
        return this;
    }

    @JsonAnySetter
    public void rejectUnknownField(String name, JsonNode value) {
        throw new InvalidCommandException();
    }
}
