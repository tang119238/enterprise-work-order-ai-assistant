package com.tangmeng.workorder.observability;

import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.Timer;
import io.micrometer.core.instrument.Counter;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/**
 * Observability configuration for metrics and tracing.
 */
@Configuration
public class TelemetryConfig {

    @Bean
    public ConnectorMetrics connectorMetrics(MeterRegistry registry) {
        return new ConnectorMetrics(registry);
    }

    @Bean
    public AnalyticsMetrics analyticsMetrics(MeterRegistry registry) {
        return new AnalyticsMetrics(registry);
    }

    public static class ConnectorMetrics {
        private final Timer connectorLatency;
        private final Counter connectorSuccess;
        private final Counter connectorTimeout;
        private final Counter connectorError;

        public ConnectorMetrics(MeterRegistry registry) {
            this.connectorLatency = Timer.builder("connector.operation.latency")
                .description("Connector operation latency")
                .register(registry);
            this.connectorSuccess = Counter.builder("connector.operation.success")
                .description("Successful connector operations")
                .register(registry);
            this.connectorTimeout = Counter.builder("connector.operation.timeout")
                .description("Connector operation timeouts")
                .register(registry);
            this.connectorError = Counter.builder("connector.operation.error")
                .description("Connector operation errors")
                .register(registry);
        }

        public Timer.Sample startTimer() {
            return Timer.start();
        }

        public void recordSuccess(Timer.Sample sample) {
            sample.stop(connectorLatency);
            connectorSuccess.increment();
        }

        public void recordTimeout(Timer.Sample sample) {
            sample.stop(connectorLatency);
            connectorTimeout.increment();
        }

        public void recordError(Timer.Sample sample) {
            sample.stop(connectorLatency);
            connectorError.increment();
        }
    }

    public static class AnalyticsMetrics {
        private final Counter queriesTotal;
        private final Counter queriesRejected;
        private final Counter queriesTimeout;
        private final Timer queryLatency;

        public AnalyticsMetrics(MeterRegistry registry) {
            this.queriesTotal = Counter.builder("analytics.query.total")
                .description("Total analytics queries")
                .register(registry);
            this.queriesRejected = Counter.builder("analytics.query.rejected")
                .description("Rejected analytics queries")
                .register(registry);
            this.queriesTimeout = Counter.builder("analytics.query.timeout")
                .description("Analytics query timeouts")
                .register(registry);
            this.queryLatency = Timer.builder("analytics.query.latency")
                .description("Analytics query latency")
                .register(registry);
        }

        public void recordQuery() {
            queriesTotal.increment();
        }

        public void recordRejected() {
            queriesRejected.increment();
        }

        public void recordTimeout() {
            queriesTimeout.increment();
        }

        public Timer.Sample startTimer() {
            return Timer.start();
        }

        public void recordLatency(Timer.Sample sample) {
            sample.stop(queryLatency);
        }
    }
}
