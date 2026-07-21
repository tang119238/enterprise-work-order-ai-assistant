package com.tangmeng.workorder.connector.http;

import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * Configuration properties for HTTP connector.
 */
@ConfigurationProperties(prefix = "connector.http")
public record HttpConnectorProperties(
    boolean enabled,
    String baseUrl,
    String authType,
    int connectTimeoutMs,
    int readTimeoutMs,
    int maxRetries
) {
    public HttpConnectorProperties {
        if (enabled && (baseUrl == null || baseUrl.isBlank())) {
            throw new IllegalArgumentException("HTTP connector base URL is required when enabled");
        }
    }
}
