package com.tangmeng.workorder.command;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.tangmeng.workorder.domain.WorkOrderEntity;
import com.tangmeng.workorder.mapper.WorkOrderMapper;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.jdbc.core.JdbcTemplate;

import java.time.LocalDateTime;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

class JdbcWorkOrderCommandRepositoryTest {

    private static final UUID TENANT = UUID.fromString("11111111-1111-1111-1111-111111111111");
    private static final UUID ORDER_ID = UUID.fromString("00000000-0000-0000-0000-000000009701");
    private static final UUID PROJECT = UUID.fromString("00000000-0000-0000-0000-000000010001");
    private static final UUID USER = UUID.fromString("00000000-0000-0000-0000-000000009001");
    private static final LocalDateTime NOW = LocalDateTime.parse("2026-07-18T02:00:00");

    @Test
    void duplicateIdentityUsesConflictSafeInsertAndLeavesTransactionUsableForReload() {
        JdbcTemplate jdbc = mock(JdbcTemplate.class);
        WorkOrderMapper mapper = mock(WorkOrderMapper.class);
        when(jdbc.update(anyString(), any(Object[].class))).thenReturn(0);
        JdbcWorkOrderCommandRepository repository = repository(jdbc, mapper);

        assertThat(repository.insertWorkOrder(order()))
            .isEqualTo(WorkOrderCommandRepository.InsertWorkOrderResult.DUPLICATE);

        ArgumentCaptor<String> sql = ArgumentCaptor.forClass(String.class);
        verify(jdbc).update(sql.capture(), any(Object[].class));
        assertThat(sql.getValue().toLowerCase()).contains("on conflict do nothing");
        verifyNoInteractions(mapper);
    }

    @Test
    void nonUniqueIntegrityFailureMapsToInvalidCommandInsteadOfDuplicate() {
        JdbcTemplate jdbc = mock(JdbcTemplate.class);
        WorkOrderMapper mapper = mock(WorkOrderMapper.class);
        DataIntegrityViolationException databaseFailure =
            new DataIntegrityViolationException("foreign key violation");
        when(mapper.insert(any(WorkOrderEntity.class))).thenThrow(databaseFailure);
        when(jdbc.update(anyString(), any(Object[].class))).thenThrow(databaseFailure);
        JdbcWorkOrderCommandRepository repository = repository(jdbc, mapper);

        assertThatThrownBy(() -> repository.insertWorkOrder(order()))
            .isInstanceOf(InvalidCommandException.class)
            .hasCause(databaseFailure);
    }

    private JdbcWorkOrderCommandRepository repository(JdbcTemplate jdbc, WorkOrderMapper mapper) {
        return new JdbcWorkOrderCommandRepository(
            jdbc, null, null, mapper, null, new ObjectMapper());
    }

    private WorkOrderEntity order() {
        return WorkOrderEntity.builder()
            .id(ORDER_ID).tenantId(TENANT).workOrderNo("WO-NEW")
            .title("new").description("description")
            .projectId(PROJECT).projectName("Project")
            .spacePath("A/1").orderType("REPAIR").priority("HIGH")
            .status("PENDING_DISPATCH").source("MANUAL").version(0L)
            .createdBy(USER).updatedBy(USER).createdAt(NOW).dueAt(NOW.plusDays(1))
            .build();
    }
}
