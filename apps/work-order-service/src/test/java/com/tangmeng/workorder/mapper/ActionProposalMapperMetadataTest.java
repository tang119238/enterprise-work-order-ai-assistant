package com.tangmeng.workorder.mapper;

import com.baomidou.mybatisplus.core.MybatisConfiguration;
import com.baomidou.mybatisplus.core.metadata.TableInfoHelper;
import com.tangmeng.workorder.domain.ActionProposalEntity;
import org.apache.ibatis.builder.MapperBuilderAssistant;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThatCode;

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
}
