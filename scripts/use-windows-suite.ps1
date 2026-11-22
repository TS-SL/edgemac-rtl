param(
    [Parameter(Mandatory)]
    [string] $SuiteRoot,

    [string] $PythonPackages
)

$SuiteRoot = (Resolve-Path -LiteralPath $SuiteRoot -ErrorAction Stop).Path
if (!(Test-Path -LiteralPath (Join-Path $SuiteRoot 'bin/iverilog.exe'))) {
    throw "Missing Icarus executable under $SuiteRoot"
}
$env:YOSYSHQ_ROOT = $SuiteRoot
$env:VERILATOR_ROOT = Join-Path $SuiteRoot 'share/verilator'
$env:PATH = "$(Join-Path $SuiteRoot 'bin');$(Join-Path $SuiteRoot 'lib');$env:PATH"
$env:EDGEMAC_ICARUS_SPLIT = '1'
if ($PythonPackages) {
    $packagesPath = (Resolve-Path -LiteralPath $PythonPackages -ErrorAction Stop).Path
    $env:PYTHONPATH = if ($env:PYTHONPATH) { "$packagesPath;$env:PYTHONPATH" } else { $packagesPath }
}
