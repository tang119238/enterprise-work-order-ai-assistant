package com.tangmeng.workorder.mapper;

import com.baomidou.mybatisplus.core.MybatisConfiguration;
import com.baomidou.mybatisplus.core.metadata.TableInfoHelper;
import com.tangmeng.workorder.domain.ActionProposalEntity;
import org.apache.ibatis.builder.MapperBuilderAssistant;
import org.apache.ibatis.annotations.Results;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThatCode;
import static org.assertj.core.api.Assertions.assertThat;

import java.util.Arrays;
import java.util.Set;
import java.util.UUID;
import java.util.stream.Collectors;

class ActionProposalMapperMetadataTest {

    @Test
    void initializesMapperMetadataWithoutRequiringAnExplicitUuidResultHandler() {
        TableInfoHelper.remove(ActionProposalEntity.class);
        MybatisConfiguration configuration = new MybatisConfiguration();

        assertThatCode(() -> TableInfoHelper.initTableInfo(
            new MapperBuilderAssistant(configuration, "proposal-metadata-test"),
            ActionProposalEntity.class
        )).doesNotThrowAnyException();
    }

    @Test
    void explicitReadResultMapCoversEverySelectedProposalProperty() throws Exception {
        Results results = ActionProposalMapper.class
            .getMethod("selectProposalById", UUID.class, UUID.class)
            .getAnnotation(Results.class);

        Set<String> mappedProperties = Arrays.stream(results.value())
            .map(org.apache.ibatis.annotations.Result::property)
            .collect(Collectors.toSet());

        assertThat(mappedProperties).containsExactlyInAnyOrder(
            "id", "tenantId", "actionType", "targetId", "commandPayload",
            "beforeSnapshot", "afterSnapshot", "riskLevel", "status",
            "requestedBy", "confirmedBy", "expectedVersion", "expiresAt",
            "executionResult", "errorCode", "createdAt", "updatedAt"
        );
    }
}
