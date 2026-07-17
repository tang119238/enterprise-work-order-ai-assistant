package com.tangmeng.workorder.service;

import com.baomidou.mybatisplus.core.metadata.IPage;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.tangmeng.workorder.domain.WorkOrderEntity;
import com.tangmeng.workorder.mapper.WorkOrderMapper;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.time.LocalDateTime;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
@SuppressWarnings("unchecked")
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

    @Test
    void returnsRootAndReworkOrdersInCreationOrder() {
        WorkOrderEntity rework = WorkOrderEntity.builder()
            .workOrderNo("WO-20260718-008")
            .rootWorkOrderNo("WO-20260718-007")
            .createdAt(LocalDateTime.parse("2026-07-18T10:00:00"))
            .build();
        WorkOrderEntity root = WorkOrderEntity.builder()
            .workOrderNo("WO-20260718-007")
            .createdAt(LocalDateTime.parse("2026-07-18T08:00:00"))
            .build();
        when(mapper.selectById("WO-20260718-008")).thenReturn(rework);
        when(mapper.selectList(any())).thenReturn(List.of(root, rework));

        List<WorkOrderEntity> result = service.reworkChain("WO-20260718-008");

        assertThat(result).extracting(WorkOrderEntity::getWorkOrderNo)
            .containsExactly("WO-20260718-007", "WO-20260718-008");
    }

    @Test
    void usesOneBasedMybatisPageForZeroBasedPublicPage() {
        Page<WorkOrderEntity> returned = Page.of(1, 20, 1);
        returned.setRecords(List.of(WorkOrderEntity.builder()
            .workOrderNo("WO-20260718-001")
            .build()));
        when(mapper.selectPage(any(Page.class), any())).thenReturn(returned);

        IPage<WorkOrderEntity> result = service.search(
            new WorkOrderSearchCriteria("PROCESSING", null, null, null, null, null),
            0,
            20
        );

        assertThat(result.getCurrent()).isEqualTo(1);
        assertThat(result.getSize()).isEqualTo(20);
        assertThat(result.getTotal()).isEqualTo(1);
    }
}
