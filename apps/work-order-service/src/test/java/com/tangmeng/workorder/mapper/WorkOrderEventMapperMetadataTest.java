package com.tangmeng.workorder.mapper;

import com.baomidou.mybatisplus.core.MybatisConfiguration;
import com.baomidou.mybatisplus.core.metadata.TableInfo;
import com.baomidou.mybatisplus.core.metadata.TableInfoHelper;
import com.tangmeng.workorder.domain.WorkOrderEventEntity;
import com.tangmeng.workorder.config.MybatisPlusConfig;
import org.apache.ibatis.builder.MapperBuilderAssistant;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

class WorkOrderEventMapperMetadataTest {

    @Test
    void jsonSnapshotsUseTheJsonbHandlerForInsertAndGeneratedReads() {
        TableInfoHelper.remove(WorkOrderEventEntity.class);
        MybatisConfiguration configuration = new MybatisConfiguration();
        new MybatisPlusConfig().uuidTypeHandlerCustomizer().customize(configuration);
        TableInfo info = TableInfoHelper.initTableInfo(
            new MapperBuilderAssistant(configuration, "event-metadata-test"),
            WorkOrderEventEntity.class);

        assertThat(info.isAutoInitResultMap()).isTrue();
        assertThat(info.getFieldList().stream()
            .filter(field -> field.getProperty().equals("beforeSnapshot") || field.getProperty().equals("afterSnapshot"))
            .map(field -> field.getTypeHandler().getSimpleName()))
            .containsExactlyInAnyOrder("JsonNodeTypeHandler", "JsonNodeTypeHandler");
    }
}
