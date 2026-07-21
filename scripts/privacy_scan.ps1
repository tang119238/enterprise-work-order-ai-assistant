# Privacy gatekeeper for public repository
# Scans source code, config, docs, and git for sensitive patterns

param(
    [string]$ScanPath = ".",
    [switch]$Verbose
)

$ErrorActionPreference = "Stop"

Write-Host "=== Privacy Scan Starting ===" -ForegroundColor Cyan

# Patterns that indicate private/sensitive content
$patterns = @{
    "Private paths" = @(
        'C:\\Projects\\java\\',
        'C:\\Projects\\private\\',
        'C:\\Users\\.*\\Documents\\',
        '/home/.*\/\.ssh/',
        'bkfctw',
        'tangtanai'
    )
    "API keys/tokens" = @(
        'sk-[a-zA-Z0-9]{20,}',
        'AKIA[0-9A-Z]{16}',
        'ghp_[a-zA-Z0-9]{36}',
        'Bearer\s+[a-zA-Z0-9\-._~+/]+=*',
        'password\s*[:=]\s*["\'][^"\']{8,}',
        'secret\s*[:=]\s*["\'][^"\']{8,}'
    )
    "Private hostnames" = @(
        '192\.168\.\d+\.\d+',
        '10\.\d+\.\d+\.\d+',
        '172\.(1[6-9]|2[0-9]|3[01])\.\d+\.\d+',
        'localhost:\d{4,5}(?!8080|8000|5432)',
        '\.internal\.',
        '\.corp\.',
        '\.local'
    )
    "Company identifiers" = @(
        '卓越',
        '某公司',
        '某某企业',
        '真实客户',
        '生产环境'
    )
}

$totalIssues = 0
$filesScanned = 0

foreach ($category in $patterns.Keys) {
    Write-Host "`nChecking: $category" -ForegroundColor Yellow

    foreach ($pattern in $patterns[$category]) {
        $results = Get-ChildItem -Path $ScanPath -Recurse -File -Include *.java,*.py,*.yml,*.yaml,*.json,*.md,*.properties,*.sql,*.xml,*.env* |
            Where-Object { $_.FullName -notmatch '\.(git|venv|node_modules|target|__pycache__)' } |
            Select-String -Pattern $pattern -ErrorAction SilentlyContinue

        if ($results) {
            foreach ($result in $results) {
                $totalIssues++
                Write-Host "  FOUND: $($result.Filename):$($result.LineNumber) matches '$pattern'" -ForegroundColor Red
                if ($Verbose) {
                    Write-Host "    Line: $($result.Line.Trim())" -ForegroundColor Gray
                }
            }
        }
    }
}

Write-Host "`n=== Privacy Scan Complete ===" -ForegroundColor Cyan
Write-Host "Files scanned: $filesScanned" -ForegroundColor White
Write-Host "Issues found: $totalIssues" -ForegroundColor $(if ($totalIssues -gt 0) { "Red" } else { "Green" })

if ($totalIssues -gt 0) {
    Write-Host "`nFAILED: Privacy violations detected. Please fix before committing." -ForegroundColor Red
    exit 1
} else {
    Write-Host "`nPASSED: No privacy violations detected." -ForegroundColor Green
    exit 0
}
