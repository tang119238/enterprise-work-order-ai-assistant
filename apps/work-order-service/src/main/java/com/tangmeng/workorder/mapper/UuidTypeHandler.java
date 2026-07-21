package com.tangmeng.workorder.mapper;

import org.apache.ibatis.type.BaseTypeHandler;
import org.apache.ibatis.type.JdbcType;

import java.sql.CallableStatement;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Types;
import java.util.UUID;

public class UuidTypeHandler extends BaseTypeHandler<UUID> {

    @Override
    public void setNonNullParameter(
        PreparedStatement statement,
        int index,
        UUID parameter,
        JdbcType jdbcType
    ) throws SQLException {
        statement.setObject(index, parameter, Types.OTHER);
    }

    @Override
    public UUID getNullableResult(ResultSet resultSet, String columnName) throws SQLException {
        return uuid(resultSet.getObject(columnName));
    }

    @Override
    public UUID getNullableResult(ResultSet resultSet, int columnIndex) throws SQLException {
        return uuid(resultSet.getObject(columnIndex));
    }

    @Override
    public UUID getNullableResult(CallableStatement statement, int columnIndex) throws SQLException {
        return uuid(statement.getObject(columnIndex));
    }

    private static UUID uuid(Object value) throws SQLException {
        if (value == null) {
            return null;
        }
        if (value instanceof UUID uuid) {
            return uuid;
        }
        try {
            return UUID.fromString(value.toString());
        } catch (IllegalArgumentException exception) {
            throw new SQLException("Invalid UUID value", exception);
        }
    }
}
