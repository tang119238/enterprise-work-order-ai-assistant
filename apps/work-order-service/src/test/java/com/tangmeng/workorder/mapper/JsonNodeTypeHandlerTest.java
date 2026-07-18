package com.tangmeng.workorder.mapper;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.apache.ibatis.type.JdbcType;
import org.junit.jupiter.api.Test;

import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.Types;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class JsonNodeTypeHandlerTest {

    private final ObjectMapper objectMapper = new ObjectMapper();
    private final JsonNodeTypeHandler handler = new JsonNodeTypeHandler();

    @Test
    void bindsJsonAsPostgresOtherRatherThanVarchar() throws Exception {
        PreparedStatement statement = mock(PreparedStatement.class);
        JsonNode value = objectMapper.readTree("{\"status\":\"PENDING\"}");

        handler.setParameter(statement, 2, value, JdbcType.OTHER);

        verify(statement).setObject(2, "{\"status\":\"PENDING\"}", Types.OTHER);
    }

    @Test
    void readsJsonbTextBackAsJsonNode() throws Exception {
        ResultSet resultSet = mock(ResultSet.class);
        when(resultSet.getString("payload")).thenReturn("{\"version\":7}");

        assertThat(handler.getResult(resultSet, "payload").get("version").asLong()).isEqualTo(7L);
    }
}
