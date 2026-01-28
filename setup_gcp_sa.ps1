param (
    [string]$ProjectId
)

$ErrorActionPreference = "Stop"

# Get Project ID
if ([string]::IsNullOrWhiteSpace($ProjectId)) {
    try {
        $current = gcloud config get-value project 2>$null
        if (-not [string]::IsNullOrWhiteSpace($current)) {
            Write-Host "Current configured project: $current" -ForegroundColor Cyan
            $input = Read-Host "Enter Project ID to use (press Enter to use '$current')"
            if ([string]::IsNullOrWhiteSpace($input)) {
                $ProjectId = $current
            } else {
                $ProjectId = $input
            }
        }
    }
    catch {
        # Ignore config error
    }
}

if ([string]::IsNullOrWhiteSpace($ProjectId)) {
    $ProjectId = Read-Host "Please enter your GCP Project ID"
}

if ([string]::IsNullOrWhiteSpace($ProjectId)) {
    Write-Error "Project ID is required."
    exit 1
}

Write-Host "Using Project ID: $ProjectId" -ForegroundColor Green

$saName = "github-deploy"
$saEmail = "$saName@$ProjectId.iam.gserviceaccount.com"
$displayName = "GitHub Actions Deploy"

# Check if SA exists
Write-Host "Checking if Service Account exists..." -ForegroundColor Cyan
$saExists = gcloud iam service-accounts list --project=$ProjectId --filter="email:$saEmail" --format="value(email)"
if (-not $saExists) {
    Write-Host "Creating Service Account: $saName..." -ForegroundColor Yellow
    gcloud iam service-accounts create $saName --project=$ProjectId --display-name="$displayName"
} else {
    Write-Host "Service Account $saName already exists." -ForegroundColor Green
}

# Grant roles
$roles = @(
    "roles/run.admin",
    "roles/storage.admin",
    "roles/iam.serviceAccountUser",
    "roles/artifactregistry.admin"
)

foreach ($role in $roles) {
    Write-Host "Granting role: $role..." -ForegroundColor Cyan
    gcloud projects add-iam-policy-binding $ProjectId `
        --member="serviceAccount:$saEmail" `
        --role="$role" `
        --condition=None `
        --quiet 2>$null | Out-Null
}

# Create Key
$keyFile = "key.json"
Write-Host "Creating JSON key file: $keyFile..." -ForegroundColor Yellow
if (Test-Path $keyFile) {
    Remove-Item $keyFile -Force
}

gcloud iam service-accounts keys create $keyFile `
    --project=$ProjectId `
    --iam-account=$saEmail

Write-Host "`nSUCCESS!" -ForegroundColor Green
Write-Host "---------------------------------------------------"
Write-Host "1. Open key.json and copy its ENTIRE content."
Write-Host "2. Go to GitHub Repo > Settings > Secrets > Actions"
Write-Host "3. Add New Repository Secret:"
Write-Host "   Name: GCP_SA_KEY"
Write-Host "   Value: [Paste content of key.json]"
Write-Host "---------------------------------------------------"
Write-Host "4. Also ensure you have GCP_PROJECT_ID secret set to: $ProjectId"
