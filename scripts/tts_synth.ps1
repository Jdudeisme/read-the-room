# Batch-synthesize utterances to WAV via WinRT OneCore TTS (sees David/Mark/Zira,
# unlike SAPI5/System.Speech which only exposes the two Desktop voices).
#
# Usage: powershell -File tts_synth.ps1 -Manifest jobs.jsonl
#   where each line is {"voice": "Mark", "text": "...", "out": "path.wav",
#                       "pitch": 1.0, "rate": 1.0}
# One process for the whole batch — per-process startup dominates otherwise.
param(
    [Parameter(Mandatory = $true)][string]$Manifest
)

$null = [Windows.Media.SpeechSynthesis.SpeechSynthesizer, Windows.Media.SpeechSynthesis, ContentType = WindowsRuntime]
$null = [Windows.Storage.Streams.DataReader, Windows.Storage.Streams, ContentType = WindowsRuntime]
Add-Type -AssemblyName System.Runtime.WindowsRuntime

$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
        $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
    })[0]
function Await($WinRtTask, $ResultType) {
    $asTask = $asTaskGeneric.MakeGenericMethod($ResultType)
    $netTask = $asTask.Invoke($null, @($WinRtTask))
    $netTask.Wait() | Out-Null
    $netTask.Result
}

$synth = New-Object Windows.Media.SpeechSynthesis.SpeechSynthesizer
$voices = [Windows.Media.SpeechSynthesis.SpeechSynthesizer]::AllVoices

Get-Content $Manifest | ForEach-Object {
    if (-not $_.Trim()) { return }
    $job = $_ | ConvertFrom-Json
    $info = $voices | Where-Object { $_.DisplayName -like "*$($job.voice)*" } | Select-Object -First 1
    if (-not $info) { throw "voice not found: $($job.voice)" }
    $synth.Voice = $info
    $synth.Options.SpeakingRate = $job.rate
    $synth.Options.AudioPitch = $job.pitch
    $stream = Await ($synth.SynthesizeTextToStreamAsync($job.text)) ([Windows.Media.SpeechSynthesis.SpeechSynthesisStream])
    $size = $stream.Size
    $reader = New-Object Windows.Storage.Streams.DataReader($stream.GetInputStreamAt(0))
    Await ($reader.LoadAsync($size)) ([UInt32]) | Out-Null
    $bytes = New-Object byte[] $size
    $reader.ReadBytes($bytes)
    [System.IO.File]::WriteAllBytes($job.out, $bytes)
    Write-Output "$($job.out) $($bytes.Length)"
}
$synth.Dispose()
