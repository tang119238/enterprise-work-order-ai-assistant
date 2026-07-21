package com.tangmeng.workorder.api;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.tangmeng.workorder.command.InvalidCommandException;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class ConfirmProposalRequestTest {

    private final ObjectMapper mapper = new ObjectMapper();

    @Test
    void acceptsOnlyTheExactDecisionForEachEndpoint() throws Exception {
        ConfirmProposalRequest confirm = mapper.readValue("{\"decision\":\"CONFIRM\"}", ConfirmProposalRequest.class);
        ConfirmProposalRequest reject = mapper.readValue("{\"decision\":\"REJECT\"}", ConfirmProposalRequest.class);

        assertThat(confirm.requireConfirm()).isSameAs(confirm);
        assertThat(reject.requireReject()).isSameAs(reject);
        assertThatThrownBy(confirm::requireReject).isInstanceOf(InvalidCommandException.class);
        assertThatThrownBy(reject::requireConfirm).isInstanceOf(InvalidCommandException.class);
    }

    @Test
    void rejectsMissingUnknownAndAuthorityFields() {
        for (String json : new String[]{
            "{}", "{\"decision\":\"confirm\"}",
            "{\"decision\":\"CONFIRM\",\"confirmed_by\":\"forged\"}",
            "{\"decision\":\"CONFIRM\",\"tenant_id\":\"forged\"}"
        }) {
            assertThatThrownBy(() -> mapper.readValue(json, ConfirmProposalRequest.class))
                .hasRootCauseInstanceOf(InvalidCommandException.class);
        }
    }
}
