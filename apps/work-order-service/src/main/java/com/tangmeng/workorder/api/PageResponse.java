package com.tangmeng.workorder.api;

import com.baomidou.mybatisplus.core.metadata.IPage;

import java.util.List;
import java.util.function.Function;

public record PageResponse<T>(
    List<T> items,
    long page,
    long size,
    long total,
    long totalPages
) {
    public static <S, T> PageResponse<T> from(IPage<S> source, Function<S, T> mapper) {
        return new PageResponse<>(
            source.getRecords().stream().map(mapper).toList(),
            Math.max(0, source.getCurrent() - 1),
            source.getSize(),
            source.getTotal(),
            source.getPages()
        );
    }
}
