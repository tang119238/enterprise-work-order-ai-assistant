package com.tangmeng.workorder.integration;

import com.tangmeng.workorder.service.WorkOrderQueryService;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.testcontainers.service.connection.ServiceConnection;
import org.springframework.jdbc.core.JdbcTemplate;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;

import static org.assertj.core.api.Assertions.assertThat;

@Testcontainers(disabledWithoutDocker = true)
@SpringBootTest
class WorkOrderPostgresIntegrationTest {

    @Container
    @ServiceConnection
    static final PostgreSQLContainer<?> POSTGRES =
        new PostgreSQLContainer<>("postgres:16-alpine");

    @Autowired
    private JdbcTemplate jdbcTemplate;

    @Autowired
    private WorkOrderQueryService service;

    @Test
    void flywaySeedsExactlyFiftyOrders() {
        Long count = jdbcTemplate.queryForObject("select count(*) from work_order", Long.class);

        assertThat(count).isEqualTo(50L);
    }

    @Test
    void seededReworkOrderResolvesItsChain() {
        assertThat(service.reworkChain("WO-20260718-008"))
            .extracting(order -> order.getWorkOrderNo())
            .containsExactly("WO-20260718-007", "WO-20260718-008");
    }
}

