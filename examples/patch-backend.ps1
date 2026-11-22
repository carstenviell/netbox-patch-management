<#
  Example script for the PowerShell backends of netbox-patch-management (pwsh_local / pwsh_ssh).

  Contract:
    pwsh -NoProfile -NonInteractive -File patch-backend.ps1 -Action <test|jobs|start|status|state>
    stdin : JSON {"action", "objects": [{key, type, id, name, custom_fields}], "job_ref", "external_id"}
    stdout: JSON (see below). Exit code != 0 = error, message on stderr.

  Replace the dummy logic with calls to your endpoint-management system.
#>
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('test', 'jobs', 'start', 'status', 'state')]
    [string]$Action
)
$ErrorActionPreference = 'Stop'
$request = [Console]::In.ReadToEnd() | ConvertFrom-Json

switch ($Action) {
    'test'   { $result = @{ info = "Example backend on $([Environment]::MachineName)" } }
    'jobs'   { $result = @{ jobs = @(@{ id = 'monthly-os-updates'; label = 'Monthly OS updates' }) } }
    'start'  {
        $obj = $request.objects[0]
        # e.g. Start-EmsJob -Computer $obj.name -Job $request.job_ref
        $result = @{ id = "job-$($obj.id)-$(Get-Date -Format yyyyMMddHHmmss)" }
    }
    'status' {
        # e.g. Get-EmsJobState -Id $request.external_id
        $result = @{ status = 'succeeded'; raw = 'Finished'; finished_at = (Get-Date).ToUniversalTime().ToString('o') }
    }
    'state'  {
        $states = @{}
        foreach ($obj in $request.objects) {
            # e.g. Get-EmsPatchState -Computer $obj.name
            $states[$obj.key] = @{ last_patched = (Get-Date).AddDays(-7).ToString('yyyy-MM-dd'); compliant = $true; detail = '' }
        }
        $result = @{ states = $states }
    }
}
$result | ConvertTo-Json -Depth 6 -Compress
