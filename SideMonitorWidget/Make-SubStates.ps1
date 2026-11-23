# Sprite sheets for the sub's mood states. Habits.lua picks one by how many habit
# labels are on fire (2 lit = the original side_monitor_animation.png).
#
#   .\Make-SubStates.ps1        # writes @Resources\sub_sleep.png / sub_slow.png / sub_zoom.png
#
#   sleep : 0 habits lit  - resting on the seabed (right side), nightcap, shutters closed, Zzz
#   slow  : 1 habit lit   - drifting slowly with the original bob, nightcap, half-shut shutters
#   zoom  : 3 habits lit  - racing left, a tentacle chasing it that grows as it closes in
# Frames are 225x70 on opaque black, same as side_monitor_animation.png.
param([string]$Src = (Join-Path $PSScriptRoot 'side_monitor_animation.png'),
      [string]$OutDir = (Join-Path $PSScriptRoot '@Resources'))

Add-Type -AssemblyName System.Drawing
$FW = 225; $FH = 70
$srcBmp = New-Object System.Drawing.Bitmap $Src

function C($r, $g, $b) { [System.Drawing.Color]::FromArgb(255, $r, $g, $b) }
$BLACK = C 0 0 0
$LID = C 242 212 9
$LID_LINE = C 0 0 255
$HAT1 = C 50 70 200; $HAT2 = C 120 150 245; $WHITE = C 235 235 235
$SAND1 = C 150 120 60; $SAND2 = C 190 160 90; $ROCK = C 90 90 90
$ZCOL = C 200 210 255
$BUB_O = C 0 114 188; $BUB_I = C 109 207 246; $BUB_W = C 255 255 255
$T_BODY = C 170 60 200; $T_DARK = C 90 20 120; $T_SUCK = C 245 160 225
$SPEED = C 150 200 255
$SWEAT = C 120 220 255

# original animation: sprite top-left per frame
$ORIG_Y = @(12, 1, 13, 30, 15)

# --- sub sprite from frame 0 (x 168..215, y 12..49), dark pixels = transparent ---
$SW = 48; $SH = 38
$sub = New-Object 'object[,]' $SW, $SH
for ($x = 0; $x -lt $SW; $x++) { for ($y = 0; $y -lt $SH; $y++) {
    $c = $srcBmp.GetPixel(168 + $x, 12 + $y)
    if (($c.R + $c.G + $c.B) -gt 30) { $sub[$x, $y] = $c } else { $sub[$x, $y] = $null }
} }

function New-Frame { $b = New-Object System.Drawing.Bitmap $FW, $FH; $gr = [System.Drawing.Graphics]::FromImage($b); $gr.Clear($BLACK); $gr.Dispose(); $b }
function Px($bmp, $x, $y, $col) { $x = [int][math]::Round($x); $y = [int][math]::Round($y); if ($x -ge 0 -and $x -lt $FW -and $y -ge 0 -and $y -lt $FH) { $bmp.SetPixel($x, $y, $col) } }
function Rect($bmp, $x, $y, $w, $h, $col) { for ($i = 0; $i -lt $w; $i++) { for ($j = 0; $j -lt $h; $j++) { Px $bmp ($x + $i) ($y + $j) $col } } }

# glass = the cyan/light-blue interior of the front window and both portholes
function Is-Glass($c) { ($c.R -eq 0 -and $c.G -eq 255 -and $c.B -eq 255) -or ($c.R -eq 109 -and $c.G -eq 207) -or ($c.R -eq 0 -and $c.G -eq 191) }

# windows: 'open' | 'half' | 'closed' (yellow shutters with a blue seam);
# hat: nightcap pulled down over the conning tower instead of the periscope
function Draw-Sub($bmp, $ox, $oy, $windows, $hat) {
    for ($x = 0; $x -lt $SW; $x++) { for ($y = 0; $y -lt $SH; $y++) {
        $c = $sub[$x, $y]; if ($null -eq $c) { continue }
        if ($hat -and $y -lt 6) { continue }            # periscope tucked away
        if ($windows -ne 'open' -and (Is-Glass $c)) {
            # front window glass spans rows 15..24, portholes rows 20..23
            if ($x -lt 17) { $top = 15; $bot = 24 } else { $top = 20; $bot = 23 }
            $seam = if ($windows -eq 'closed') { [int](($top + $bot) / 2) } else { $top + [int](($bot - $top) / 3) }
            if ($y -lt $seam) { $c = $LID } elseif ($y -eq $seam) { $c = $LID_LINE } elseif ($windows -eq 'closed') { $c = $LID }
        }
        Px $bmp ($ox + $x) ($oy + $y) $c
    } }
    if ($hat) {
        # striped nightcap pulled over the tower (tower = rows 6..12, x 13..21),
        # white fur band at the brim, tip flopping down the back with a pom-pom
        $rows = @(
            @(2, 19, 25, 1), @(3, 17, 27, 2), @(4, 16, 22, 1), @(4, 27, 28, 1),
            @(5, 15, 22, 2), @(5, 28, 29, 2), @(6, 14, 22, 1), @(7, 14, 22, 2),
            @(8, 13, 22, 1), @(9, 13, 22, 2), @(10, 12, 23, 'band'), @(11, 12, 23, 'band')
        )
        foreach ($r in $rows) {
            $col = if ($r[3] -eq 'band') { $WHITE } elseif ($r[3] -eq 1) { $HAT1 } else { $HAT2 }
            for ($x = $r[1]; $x -le $r[2]; $x++) { Px $bmp ($ox + $x) ($oy + $r[0]) $col }
        }
        Rect $bmp ($ox + 28) ($oy + 6) 3 3 $WHITE        # pom-pom hanging off the back
    }
}

function Draw-Bubble($bmp, $x, $y, $size) {
    if ($size -le 1) { Rect $bmp $x $y 2 2 $BUB_I; return }
    Rect $bmp ($x + 1) $y 4 1 $BUB_O; Rect $bmp ($x + 1) ($y + 5) 4 1 $BUB_O
    Rect $bmp $x ($y + 1) 1 4 $BUB_O; Rect $bmp ($x + 5) ($y + 1) 1 4 $BUB_O
    Rect $bmp ($x + 1) ($y + 1) 4 4 $BUB_I; Rect $bmp ($x + 1) ($y + 1) 2 1 $BUB_W; Px $bmp ($x + 1) ($y + 2) $BUB_W
}

$Z3 = @('###', '.#.', '###')
$Z5 = @('#####', '...#.', '..#..', '.#...', '#####')
function Draw-Glyph($bmp, $x, $y, $glyph, $col) {
    for ($j = 0; $j -lt $glyph.Count; $j++) { for ($i = 0; $i -lt $glyph[$j].Length; $i++) {
        if ($glyph[$j][$i] -eq '#') { Px $bmp ($x + $i) ($y + $j) $col }
    } }
}

function Draw-Seabed($bmp) {
    for ($x = 0; $x -lt $FW; $x++) {
        $h = 3 + [int](1.2 * [math]::Sin($x / 9.0) + 0.8 * [math]::Sin($x / 3.7))
        for ($y = $FH - $h; $y -lt $FH; $y++) { Px $bmp $x $y $(if ((($x * 7 + $y * 3) % 5) -eq 0) { $SAND2 } else { $SAND1 }) }
    }
    foreach ($rx in 30, 80, 125) { Rect $bmp $rx ($FH - 6) 4 2 $ROCK; Rect $bmp ($rx + 1) ($FH - 7) 2 1 $ROCK }
}

function Save-Sheet($frames, $name) {
    $sheet = New-Object System.Drawing.Bitmap ($FW * $frames.Count), $FH
    $gr = [System.Drawing.Graphics]::FromImage($sheet)
    for ($i = 0; $i -lt $frames.Count; $i++) { $gr.DrawImageUnscaled($frames[$i], $i * $FW, 0); $frames[$i].Dispose() }
    $gr.Dispose()
    $sheet.Save((Join-Path $OutDir $name), [System.Drawing.Imaging.ImageFormat]::Png)
    $sheet.Dispose()
}

# ---------------- sleep: 5 frames, parked on the right ----------------
$frames = @()
for ($f = 0; $f -lt 5; $f++) {
    $b = New-Frame
    Draw-Seabed $b
    $breath = @(0, 0, 1, 1, 0)[$f]
    $sx = 150; $sy = $FH - 4 - $SH + 1 - $breath
    Draw-Sub $b $sx $sy 'closed' $true
    foreach ($z in @(0, 2, 4)) {
        $age = ($f + 5 - $z) % 5
        Draw-Glyph $b ($sx + 22 + $age * 3) ($sy - 3 - $age * 5) $(if ($age -lt 2) { $Z3 } else { $Z5 }) $ZCOL
    }
    $frames += $b
}
Save-Sheet $frames 'sub_sleep.png'

# ---------------- slow: 10 frames, same bob as the original, ~1/3 the speed ----------------
function Lerp-Orig($arr, $p) {
    # sample the original 5-frame y path (looping) at fractional frame p
    $i = [math]::Floor($p); $t = $p - $i
    $a = $arr[[int]$i % 5]; $bb = $arr[([int]$i + 1) % 5]
    $a + ($bb - $a) * $t
}
$frames = @()
for ($f = 0; $f -lt 10; $f++) {
    $b = New-Frame
    $sx = 165 - $f * 11
    $sy = [int][math]::Round((Lerp-Orig $ORIG_Y ($f / 2.0)))
    if ($sy -lt 4) { $sy = 4 }                            # keep the cap on screen
    Draw-Sub $b $sx $sy 'half' $true
    Draw-Bubble $b ($sx + 50 + ($f % 2)) ($sy + 20 - ($f % 3) * 3) 1
    if ($f % 2 -eq 0) { Draw-Bubble $b ($sx + 56) ($sy + 12 - ($f % 4)) 2 }
    if ($f -ge 6) { Draw-Glyph $b ($sx + 30 + ($f - 6) * 2) ([math]::Max(0, $sy - 1 - ($f - 6) * 3)) $Z3 $ZCOL }
    $frames += $b
}
Save-Sheet $frames 'sub_slow.png'

# ---------------- zoom: 5 frames ----------------
function Draw-Tentacle($bmp, $tipX, $tipY, $wave, $scale) {
    $w = $FW; $h = $FH
    $mask = New-Object 'int[,]' $w, $h      # 0 none, 1 body, 2 sucker
    $p0x = 250; $p0y = 92; $p1x = ($tipX + 250) / 2 + 10; $p1y = $tipY + 5 + $wave
    $pts = @()
    for ($t = 0.0; $t -le 1.0; $t += 0.01) {
        $u = 1 - $t
        $x = $u * $u * $p0x + 2 * $u * $t * $p1x + $t * $t * $tipX
        $y = $u * $u * $p0y + 2 * $u * $t * $p1y + $t * $t * $tipY
        $pts += , @($x, $y, ($scale * (12 - 9 * $t) + 0.8))
    }
    $cr = 2 + 4 * $scale
    for ($a = 0; $a -le 260; $a += 10) {
        $rad = (90 + $a) * [math]::PI / 180
        $pts += , @(($tipX + $cr * [math]::Cos($rad)), ($tipY - $cr + $cr * [math]::Sin($rad)), (1.2 + 1.4 * $scale * (1 - $a / 260.0)))
    }
    foreach ($p in $pts) {
        $r = $p[2]
        for ($x = [int]($p[0] - $r - 1); $x -le [int]($p[0] + $r + 1); $x++) {
            for ($y = [int]($p[1] - $r - 1); $y -le [int]($p[1] + $r + 1); $y++) {
                if ($x -lt 0 -or $x -ge $w -or $y -lt 0 -or $y -ge $h) { continue }
                if ((($x - $p[0]) * ($x - $p[0]) + ($y - $p[1]) * ($y - $p[1])) -le $r * $r) { $mask[$x, $y] = 1 }
            }
        }
    }
    for ($i = 8; $i -lt 90; $i += 9) {
        $a = $pts[$i]; $bb = $pts[$i + 1]
        if ($a[2] -lt 3) { continue }
        $dx = $bb[0] - $a[0]; $dy = $bb[1] - $a[1]; $len = [math]::Sqrt($dx * $dx + $dy * $dy); if ($len -eq 0) { continue }
        $nx = $dy / $len; $ny = -$dx / $len
        $sxp = [int][math]::Round($a[0] + $nx * ($a[2] - 2.5)); $syp = [int][math]::Round($a[1] + $ny * ($a[2] - 2.5))
        foreach ($o in @(@(0, 0), @(1, 0), @(0, 1), @(1, 1))) {
            $xx = $sxp + $o[0]; $yy = $syp + $o[1]
            if ($xx -ge 0 -and $xx -lt $w -and $yy -ge 0 -and $yy -lt $h -and $mask[$xx, $yy] -eq 1) { $mask[$xx, $yy] = 2 }
        }
    }
    for ($x = 0; $x -lt $w; $x++) { for ($y = 0; $y -lt $h; $y++) {
        $m = $mask[$x, $y]; if ($m -eq 0) { continue }
        $edge = $false
        foreach ($o in @(@(-1, 0), @(1, 0), @(0, -1), @(0, 1))) {
            $xx = $x + $o[0]; $yy = $y + $o[1]
            if ($xx -ge 0 -and $xx -lt $w -and $yy -ge 0 -and $yy -lt $h -and $mask[$xx, $yy] -eq 0) { $edge = $true }
        }
        $col = if ($edge) { $T_DARK } elseif ($m -eq 2) { $T_SUCK } else { $T_BODY }
        $bmp.SetPixel($x, $y, $col)
    } }
}

$frames = @()
$ZX = @(165, 125, 90, 55, 18)            # faster than the original sweep
for ($f = 0; $f -lt 5; $f++) {
    $b = New-Frame
    $sx = $ZX[$f]; $sy = $ORIG_Y[$f]
    $scale = 0.35 + 0.65 * $f / 4                          # tentacle grows as it closes in
    $tipX = [math]::Min(222, $sx + 62 + 6 * $f); $tipY = $sy + 16
    Draw-Tentacle $b $tipX $tipY (@(0, 6, -4, 3, -2)[$f]) $scale
    for ($k = 0; $k -lt 6; $k++) {
        $ly = $sy + 6 + $k * 5 + (($k * 3 + $f) % 3)
        $lx = $sx + 50 + (($k * 17 + $f * 13) % 14)
        $len = 6 + (($k * 5 + $f * 3) % 10)
        for ($i = 0; $i -lt $len; $i++) { if (($lx + $i) -lt $tipX - 6) { Px $b ($lx + $i) $ly $SPEED } }
    }
    Draw-Sub $b $sx $sy 'open' $false
    Draw-Bubble $b ($sx + 50) ($sy + 15 + ($f % 2) * 3) 1
    if ($f % 2 -eq 0) { Px $b ($sx + 25) ($sy + 2) $SWEAT; Rect $b ($sx + 24) ($sy + 3) 3 2 $SWEAT }
    $frames += $b
}
Save-Sheet $frames 'sub_zoom.png'
$srcBmp.Dispose()
'done'

