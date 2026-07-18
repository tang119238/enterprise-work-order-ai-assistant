package com.tangmeng.workorder.integration;

import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;

import static org.assertj.core.api.Assertions.assertThat;

class DatabaseRoleContractTest {

    @Test
    void composeAndInitScriptDefineSeparatedRuntimeRoles() throws IOException {
        Path initScript = Path.of("../../infra/postgres/init/001_roles.sql");
        assertThat(initScript).exists();

        String init = Files.readString(initScript);
        String compose = Files.readString(Path.of("../../docker-compose.yml"));

        assertThat(init).contains("flyway_owner", "work_order_app", "ai_app", "analytics_reader");
        assertThat(compose).contains("DB_USERNAME: work_order_app", "FLYWAY_USER: flyway_owner");
    }
}
