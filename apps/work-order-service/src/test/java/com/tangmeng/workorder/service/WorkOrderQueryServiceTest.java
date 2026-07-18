package com.tangmeng.workorder.service;

import com.baomidou.mybatisplus.core.conditions.AbstractWrapper;
import com.baomidou.mybatisplus.core.conditions.Wrapper;
import com.baomidou.mybatisplus.core.metadata.IPage;
import com.baomidou.mybatisplus.core.metadata.TableInfoHelper;
import com.baomidou.mybatisplus.core.MybatisConfiguration;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.tangmeng.workorder.domain.WorkOrderEntity;
import com.tangmeng.workorder.mapper.WorkOrderMapper;
import com.tangmeng.workorder.security.TenantContext;
import com.tangmeng.workorder.tenant.TenantTransaction;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.apache.ibatis.builder.MapperBuilderAssistant;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Set;
import java.util.UUID;
import java.util.function.Supplier;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.doAnswer;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
@SuppressWarnings({"unchecked", "rawtypes"})
class WorkOrderQueryServiceTest {

    private static final UUID TENANT = UUID.fromString("11111111-1111-1111-1111-111111111111");
    private static final UUID USER = UUID.fromString("00000000-0000-0000-0000-000000009001");
    private static final UUID PROJECT_A = UUID.fromString("00000000-0000-0000-0000-000000010001");
    private static final UUID PROJECT_B = UUID.fromString("00000000-0000-0000-0000-000000010002");
    private static final TenantContext CONTEXT = context(Set.of(PROJECT_A, PROJECT_B));

    @Mock
    private WorkOrderMapper mapper;

    @Mock
    private TenantTransaction transactions;

    private WorkOrderQueryService service;

    @BeforeAll
    static void initializeMybatisMetadata() {
        TableInfoHelper.initTableInfo(
            new MapperBuilderAssistant(new MybatisConfiguration(), "unit-test"),
            WorkOrderEntity.class
        );
    }

    @BeforeEach
    void setUp() {
        service = new WorkOrderQueryService(mapper, transactions);
        doAnswer(invocation -> ((Supplier<?>) invocation.getArgument(1)).get())
            .when(transactions).required(any(TenantContext.class), any());
    }

    @Test
    void getsOrderOnlyInsideVerifiedTenantAndProjects() {
        WorkOrderEntity entity = WorkOrderEntity.builder()
            .id(UUID.fromString("00000000-0000-0000-0000-000000000001"))
            .tenantId(TENANT)
            .projectId(PROJECT_A)
            .workOrderNo("WO-20260718-001")
            .build();
        when(mapper.selectOne(any())).thenReturn(entity);

        assertThat(service.get(CONTEXT, "WO-20260718-001")).isSameAs(entity);

        verify(transactions).required(eq(CONTEXT), any());
        ArgumentCaptor<Wrapper<WorkOrderEntity>> query = wrapperCaptor();
        verify(mapper).selectOne(query.capture());
        assertTenantAndProjects(query.getValue());
        assertThat(parameters(query.getValue())).contains("WO-20260718-001");
        verify(mapper, never()).selectById(any());
    }

    @Test
    void hidesOrderWhenVerifiedProjectSetIsEmpty() {
        TenantContext emptyProjects = context(Set.of());

        assertThatThrownBy(() -> service.get(emptyProjects, "WO-20260718-001"))
            .isInstanceOf(WorkOrderNotFoundException.class)
            .hasMessageContaining("WO-20260718-001");

        verify(transactions).required(eq(emptyProjects), any());
        verifyNoInteractions(mapper);
    }

    @Test
    void throwsStableExceptionWhenScopedOrderDoesNotExist() {
        when(mapper.selectOne(any())).thenReturn(null);

        assertThatThrownBy(() -> service.get(CONTEXT, "WO-20260718-999"))
            .isInstanceOf(WorkOrderNotFoundException.class)
            .hasMessageContaining("WO-20260718-999");
    }

    @Test
    void returnsRootAndReworkOrdersUsingRootUuidAndScopedQueries() {
        UUID rootId = UUID.fromString("00000000-0000-0000-0000-000000000007");
        WorkOrderEntity rework = WorkOrderEntity.builder()
            .id(UUID.fromString("00000000-0000-0000-0000-000000000008"))
            .tenantId(TENANT)
            .projectId(PROJECT_A)
            .workOrderNo("WO-20260718-008")
            .rootWorkOrderId(rootId)
            .rootWorkOrderNo("WO-20260718-007")
            .createdAt(LocalDateTime.parse("2026-07-18T10:00:00"))
            .build();
        WorkOrderEntity root = WorkOrderEntity.builder()
            .id(rootId)
            .tenantId(TENANT)
            .projectId(PROJECT_A)
            .workOrderNo("WO-20260718-007")
            .createdAt(LocalDateTime.parse("2026-07-18T08:00:00"))
            .build();
        when(mapper.selectOne(any())).thenReturn(rework);
        when(mapper.selectList(any())).thenReturn(List.of(root, rework));

        List<WorkOrderEntity> result = service.reworkChain(CONTEXT, "WO-20260718-008");

        assertThat(result).extracting(WorkOrderEntity::getWorkOrderNo)
            .containsExactly("WO-20260718-007", "WO-20260718-008");
        verify(transactions).required(eq(CONTEXT), any());

        ArgumentCaptor<Wrapper<WorkOrderEntity>> detail = wrapperCaptor();
        verify(mapper).selectOne(detail.capture());
        assertTenantAndProjects(detail.getValue());

        ArgumentCaptor<Wrapper<WorkOrderEntity>> chain = wrapperCaptor();
        verify(mapper).selectList(chain.capture());
        assertTenantAndProjects(chain.getValue());
        assertThat(parameters(chain.getValue()))
            .contains(rootId)
            .doesNotContain("WO-20260718-007");
    }

    @Test
    void searchScopesPageTotalToVerifiedTenantAndProjects() {
        Page<WorkOrderEntity> returned = Page.of(1, 20, 1);
        returned.setRecords(List.of(WorkOrderEntity.builder()
            .id(UUID.fromString("00000000-0000-0000-0000-000000000001"))
            .tenantId(TENANT)
            .projectId(PROJECT_A)
            .workOrderNo("WO-20260718-001")
            .build()));
        when(mapper.selectPage(any(Page.class), any())).thenReturn(returned);

        IPage<WorkOrderEntity> result = service.search(
            CONTEXT,
            new WorkOrderSearchCriteria("PROCESSING", null, null, null, null, null),
            0,
            20
        );

        assertThat(result.getCurrent()).isEqualTo(1);
        assertThat(result.getSize()).isEqualTo(20);
        assertThat(result.getTotal()).isEqualTo(1);
        verify(transactions).required(eq(CONTEXT), any());

        ArgumentCaptor<Wrapper<WorkOrderEntity>> query = wrapperCaptor();
        verify(mapper).selectPage(any(Page.class), query.capture());
        assertTenantAndProjects(query.getValue());
    }

    @Test
    void emptyVerifiedProjectSetProducesAnEmptyPageWithoutMapperAccess() {
        TenantContext emptyProjects = context(Set.of());

        IPage<WorkOrderEntity> result = service.search(
            emptyProjects,
            new WorkOrderSearchCriteria(null, null, null, null, null, null),
            2,
            10
        );

        assertThat(result.getRecords()).isEmpty();
        assertThat(result.getTotal()).isZero();
        assertThat(result.getCurrent()).isEqualTo(3);
        assertThat(result.getSize()).isEqualTo(10);
        verify(transactions).required(eq(emptyProjects), any());
        verifyNoInteractions(mapper);
    }

    private static TenantContext context(Set<UUID> projects) {
        return new TenantContext(TENANT, USER, "dispatcher-1", Set.of("DISPATCHER"), projects,
            Set.of("work-order:read"), "request-test", "trace-test");
    }

    private static ArgumentCaptor<Wrapper<WorkOrderEntity>> wrapperCaptor() {
        return ArgumentCaptor.forClass((Class) Wrapper.class);
    }

    private static void assertTenantAndProjects(Wrapper<WorkOrderEntity> wrapper) {
        assertThat(wrapper.getSqlSegment()).contains("tenant_id", "project_id IN");
        assertThat(parameters(wrapper)).contains(TENANT, PROJECT_A, PROJECT_B);
    }

    private static List<Object> parameters(Wrapper<WorkOrderEntity> wrapper) {
        return ((AbstractWrapper<?, ?, ?>) wrapper).getParamNameValuePairs().values().stream().toList();
    }
}
