package com.tangmeng.workorder.integration;

import org.junit.jupiter.api.Test;
import org.springframework.core.io.ClassPathResource;

import java.io.IOException;
import java.nio.charset.StandardCharsets;

import static org.assertj.core.api.Assertions.assertThat;

class MigrationContractTest {

    @Test
    void migrationsCreateSchemaAndExactlyFiftyDeterministicRows() throws IOException {
        ClassPathResource schema = new ClassPathResource(
            "db/migration/V1__create_work_orders.sql"
        );
        ClassPathResource seed = new ClassPathResource(
            "db/migration/V2__seed_synthetic_work_orders.sql"
        );

        assertThat(schema.exists()).isTrue();
        assertThat(seed.exists()).isTrue();

        String schemaSql = schema.getContentAsString(StandardCharsets.UTF_8);
        String seedSql = seed.getContentAsString(StandardCharsets.UTF_8);
        assertThat(schemaSql).contains("CREATE TABLE work_order", "root_work_order_no");
        assertThat(seedSql)
            .contains("generate_series(1, 50)")
            .contains("ARRAY[8, 18, 28, 38, 48]")
            .contains("ON CONFLICT (work_order_no) DO NOTHING");
    }
}

