package com.tangmeng.workorder.mapper;

import org.junit.jupiter.api.Test;

import java.sql.ResultSet;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class UuidTypeHandlerTest {

    @Test
    void readsPostgresUuidObjects() throws Exception {
        UUID expected = UUID.fromString("00000000-0000-0000-0000-000000009301");
        ResultSet resultSet = mock(ResultSet.class);
        when(resultSet.getObject("id")).thenReturn(expected);

        assertThat(new UuidTypeHandler().getResult(resultSet, "id")).isEqualTo(expected);
    }
}
