package com.tangmeng.workorder.service;

import com.tangmeng.workorder.domain.WorkOrderEntity;
import com.tangmeng.workorder.mapper.WorkOrderMapper;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class WorkOrderQueryServiceTest {

    @Mock
    private WorkOrderMapper mapper;

    @InjectMocks
    private WorkOrderQueryService service;

    @Test
    void returnsOrderByNumber() {
        WorkOrderEntity entity = WorkOrderEntity.builder()
            .workOrderNo("WO-20260718-001")
            .status("PENDING_ACCEPTANCE")
            .assigneeName("林晓")
            .build();
        when(mapper.selectById("WO-20260718-001")).thenReturn(entity);

        assertThat(service.get("WO-20260718-001")).isSameAs(entity);
    }

    @Test
    void throwsStableExceptionWhenOrderDoesNotExist() {
        when(mapper.selectById("WO-20260718-999")).thenReturn(null);

        assertThatThrownBy(() -> service.get("WO-20260718-999"))
            .isInstanceOf(WorkOrderNotFoundException.class)
            .hasMessageContaining("WO-20260718-999");
    }
}

