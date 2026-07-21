package com.tangmeng.workorder.analytics;

import com.zaxxer.hikari.HikariConfig;
import com.zaxxer.hikari.HikariDataSource;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

import javax.sql.DataSource;

/**
 * Separate read-only DataSource for analytics queries.
 * Uses analytics_reader role with no write permissions.
 */
@Configuration
public class AnalyticsDataSourceConfig {

    @Value("${analytics.datasource.url:${spring.datasource.url}}")
    private String url;

    @Value("${analytics.datasource.username:analytics_reader}")
    private String username;

    @Value("${analytics.datasource.password:}")
    private String password;

    @Value("${analytics.datasource.maximum-pool-size:5}")
    private int maxPoolSize;

    @Value("${analytics.datasource.connection-timeout:5000}")
    private long connectionTimeout;

    @Bean("analyticsDataSource")
    public DataSource analyticsDataSource() {
        HikariConfig config = new HikariConfig();
        config.setJdbcUrl(url);
        config.setUsername(username);
        config.setPassword(password);
        config.setMaximumPoolSize(maxPoolSize);
        config.setConnectionTimeout(connectionTimeout);
        config.setReadOnly(true);
        config.setPoolName("analytics-reader");
        config.addDataSourceProperty("ApplicationName", "analytics-reader");
        return new HikariDataSource(config);
    }
}
