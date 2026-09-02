param(
    [string]$ClientJsonPath = "",
    [string]$Repo = "olegmed1-art/bridge-video-free"
)

$ErrorActionPreference = "Stop"

function Base64Url([byte[]]$bytes) {
    return [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+','-').Replace('/','_')
}

function RandomBytes([int]$count) {
    $bytes = New-Object byte[] $count
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    return $bytes
}

function UrlEncode([string]$value) {
    return [Uri]::EscapeDataString($value)
}

function ParseQuery([string]$query) {
    $result = @{}
    $q = $query.TrimStart('?')
    if (-not $q) { return $result }
    foreach ($pair in $q.Split('&')) {
        if (-not $pair) { continue }
        $parts = $pair.Split('=', 2)
        $key = [Uri]::UnescapeDataString($parts[0].Replace('+',' '))
        $value = if ($parts.Count -gt 1) { [Uri]::UnescapeDataString($parts[1].Replace('+',' ')) } else { "" }
        $result[$key] = $value
    }
    return $result
}

if (-not $ClientJsonPath) {
    $candidates = @()
    $candidates += Get-ChildItem -Path $PWD -Filter "client_secret_*.json" -File -ErrorAction SilentlyContinue
    $candidates += Get-ChildItem -Path "$HOME\Downloads" -Filter "client_secret_*.json" -File -ErrorAction SilentlyContinue
    $candidates = @($candidates | Sort-Object LastWriteTime -Descending)
    if ($candidates.Count -eq 0) {
        throw "OAuth client JSON not found. Pass -ClientJsonPath <path-to-client_secret.json>."
    }
    $ClientJsonPath = $candidates[0].FullName
}

$raw = Get-Content -Raw -LiteralPath $ClientJsonPath | ConvertFrom-Json
$app = if ($raw.installed) { $raw.installed } elseif ($raw.web) { $raw.web } else { throw "Unsupported OAuth client JSON." }
$clientId = [string]$app.client_id
$clientSecret = [string]$app.client_secret
if (-not $clientId -or -not $clientSecret) { throw "client_id/client_secret missing." }

# Reserve an ephemeral loopback port. Desktop OAuth clients support this flow.
$listener = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, 0)
$listener.Start()
$port = ([System.Net.IPEndPoint]$listener.LocalEndpoint).Port
$redirectUri = "http://127.0.0.1:$port/"

# PKCE + CSRF state.
$verifier = Base64Url (RandomBytes 64)
$sha = [System.Security.Cryptography.SHA256]::Create()
try { $challenge = Base64Url ($sha.ComputeHash([Text.Encoding]::ASCII.GetBytes($verifier))) } finally { $sha.Dispose() }
$state = Base64Url (RandomBytes 24)
$scope = "https://www.googleapis.com/auth/drive"

$query = @(
    "client_id=$(UrlEncode $clientId)",
    "redirect_uri=$(UrlEncode $redirectUri)",
    "response_type=code",
    "scope=$(UrlEncode $scope)",
    "access_type=offline",
    "prompt=consent",
    "include_granted_scopes=true",
    "state=$(UrlEncode $state)",
    "code_challenge=$(UrlEncode $challenge)",
    "code_challenge_method=S256"
) -join '&'
$authUrl = "https://accounts.google.com/o/oauth2/v2/auth?$query"

Write-Host "Opening Google authorization in your browser..."
Start-Process $authUrl
Write-Host "Waiting for Google authorization callback on localhost..."

try {
    $client = $listener.AcceptTcpClient()
    $stream = $client.GetStream()
    $reader = New-Object System.IO.StreamReader($stream)
    $requestLine = $reader.ReadLine()
    while (($line = $reader.ReadLine()) -ne "" -and $null -ne $line) { }

    if (-not $requestLine -or $requestLine -notmatch '^GET\s+([^\s]+)\s+HTTP/') {
        throw "Invalid localhost OAuth callback."
    }
    $target = $matches[1]
    $callback = [Uri]("http://127.0.0.1:$port" + $target)
    $params = ParseQuery $callback.Query

    $html = "<html><body style='font-family:sans-serif'><h2>Bridge Video OAuth complete</h2><p>You can close this browser tab and return to PowerShell.</p></body></html>"
    $body = [Text.Encoding]::UTF8.GetBytes($html)
    $headers = "HTTP/1.1 200 OK`r`nContent-Type: text/html; charset=utf-8`r`nContent-Length: $($body.Length)`r`nConnection: close`r`n`r`n"
    $head = [Text.Encoding]::ASCII.GetBytes($headers)
    $stream.Write($head,0,$head.Length)
    $stream.Write($body,0,$body.Length)
    $stream.Flush()
    $client.Close()
} finally {
    $listener.Stop()
}

if ($params["error"]) { throw "Google OAuth error: $($params['error'])" }
if ($params["state"] -ne $state) { throw "OAuth state mismatch." }
$code = [string]$params["code"]
if (-not $code) { throw "Authorization code missing." }

$tokenBody = @{
    client_id = $clientId
    client_secret = $clientSecret
    code = $code
    code_verifier = $verifier
    grant_type = "authorization_code"
    redirect_uri = $redirectUri
}
$token = Invoke-RestMethod -Method Post -Uri "https://oauth2.googleapis.com/token" -ContentType "application/x-www-form-urlencoded" -Body $tokenBody
if (-not $token.refresh_token) {
    throw "Google did not return a refresh token. Revoke prior app access and run again with consent."
}

$packed = [ordered]@{
    client_id = $clientId
    client_secret = $clientSecret
    refresh_token = [string]$token.refresh_token
} | ConvertTo-Json -Compress

Set-Clipboard -Value $packed
$out = Join-Path $HOME "bridge-drive-oauth-secret.json"
Set-Content -LiteralPath $out -Value $packed -Encoding UTF8

$gh = Get-Command gh -ErrorAction SilentlyContinue
$secretSet = $false
if ($gh) {
    try {
        $packed | & gh secret set GOOGLE_DRIVE_OAUTH_JSON --repo $Repo 2>$null
        if ($LASTEXITCODE -eq 0) { $secretSet = $true }
    } catch { }
}

Write-Host ""
Write-Host "SUCCESS: Google Drive refresh token obtained."
if ($secretSet) {
    Write-Host "GitHub secret GOOGLE_DRIVE_OAUTH_JSON was set automatically for $Repo."
} else {
    Write-Host "GOOGLE_DRIVE_OAUTH_JSON is copied to the clipboard."
    Write-Host "Create one GitHub Actions repository secret named GOOGLE_DRIVE_OAUTH_JSON and paste the clipboard value."
}
Write-Host "A local backup was saved to: $out"
Write-Host "Do not commit the backup or its contents to the repository."
