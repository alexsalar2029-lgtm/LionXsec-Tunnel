[CmdletBinding()]
param(
    [string]$OutputDirectory = (Join-Path $PSScriptRoot "dist\youtube-uploader"),
    [switch]$Zip,
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
$builder = Join-Path $PSScriptRoot "make_uploader_package.py"
$arguments = @($builder, "--output-directory", $OutputDirectory)
if ($Zip) { $arguments += "--zip" }
if ($Clean) { $arguments += "--clean" }

if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 @arguments
} else {
    & python @arguments
}
exit $LASTEXITCODE
