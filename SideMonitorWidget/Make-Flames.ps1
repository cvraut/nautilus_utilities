# Generates the still pixel-art flame images shown behind the habit labels
# (COMMITS / SKIPS / WORDS) when today's value beats yesterday's.
#
#   .\Make-Flames.ps1            # writes @Resources\flame_<name>.png
#
# Each flame is drawn on a grid of chunky CELL x CELL pixels (no anti-aliasing), so
# it reads as pixel art on the side panel. Shape: a solid bed behind the text with
# jagged tongues rising above it; color bands go dark red at the outline to orange
# and yellow inside. A fixed seed per label keeps the output stable between runs.

param([string]$OutDir = (Join-Path $PSScriptRoot '@Resources'))

Add-Type -AssemblyName System.Drawing

$CELL = 2          # screen pixels per flame pixel
$TONGUE = 10       # max tongue height above the bed, in cells
$BED = 14          # bed height (covers the text), in cells

# name, width in screen px (label text width + padding), seed
$labels = @(
    @{ Name = 'commits'; W = 120; Seed = 7 },
    @{ Name = 'skips';   W = 92;  Seed = 11 },
    @{ Name = 'words';   W = 92;  Seed = 23 }
)

# outline -> core
$palette = @(
    [System.Drawing.Color]::FromArgb(255, 120, 10, 10),
    [System.Drawing.Color]::FromArgb(255, 200, 35, 20),
    [System.Drawing.Color]::FromArgb(255, 240, 80, 25),
    [System.Drawing.Color]::FromArgb(255, 255, 140, 30),
    [System.Drawing.Color]::FromArgb(255, 255, 190, 50)
)

New-Item -ItemType Directory -Force $OutDir | Out-Null

foreach ($l in $labels) {
    $rng = New-Object System.Random $l.Seed
    $cols = [int]($l.W / $CELL)
    $rows = $BED + $TONGUE + 2
    # top edge per column: tongue peaks at random spots, tapering to the bed
    $top = New-Object 'int[]' $cols
    for ($c = 0; $c -lt $cols; $c++) { $top[$c] = $TONGUE + 2 }
    $c = 1
    while ($c -lt $cols - 1) {
        $h = $rng.Next(5, $TONGUE + 1)
        $half = $rng.Next(3, 6)
        for ($k = -$half; $k -le $half; $k++) {
            $x = $c + $k
            if ($x -lt 0 -or $x -ge $cols) { continue }
            $t = $TONGUE + 2 - [int][math]::Round($h * [math]::Pow(1 - [math]::Abs($k) / ($half + 1), 1.8))
            if ($t -lt $top[$x]) { $top[$x] = $t }
        }
        $c += $half + $rng.Next(1, 4)
    }
    # rounded bed corners
    $top[0] = [math]::Max($top[0], $TONGUE + 4)
    $top[$cols - 1] = [math]::Max($top[$cols - 1], $TONGUE + 4)

    $bmp = New-Object System.Drawing.Bitmap ($cols * $CELL), ($rows * $CELL)
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.Clear([System.Drawing.Color]::Transparent)
    for ($x = 0; $x -lt $cols; $x++) {
        for ($y = $top[$x]; $y -lt $rows; $y++) {
            # depth = distance to the nearest edge (top of this column, sides, bottom)
            $d = [math]::Min($y - $top[$x], [math]::Min($x, $cols - 1 - $x))
            $d = [math]::Min($d, $rows - 1 - $y)
            # neighbours' tops also count, so tongue sides get an outline too
            if ($x -gt 0) { $d = [math]::Min($d, $y - $top[$x - 1] + 1) }
            if ($x -lt $cols - 1) { $d = [math]::Min($d, $y - $top[$x + 1] + 1) }
            $d = [math]::Max(0, [math]::Min($d, 3))
            # a yellow glow band follows the flame contour; the bed behind the white
            # text stays orange so the label keeps its contrast
            if ($d -ge 2 -and $y -lt $top[$x] + 6 -and $y -lt $TONGUE + 7) { $d = 4 }
            $brush = New-Object System.Drawing.SolidBrush $palette[$d]
            $g.FillRectangle($brush, $x * $CELL, $y * $CELL, $CELL, $CELL)
            $brush.Dispose()
        }
    }
    # a few loose embers above the tongues
    for ($i = 0; $i -lt 3; $i++) {
        $x = $rng.Next(2, $cols - 2)
        $y = $top[$x] - $rng.Next(2, 4)
        if ($y -ge 0) {
            $brush = New-Object System.Drawing.SolidBrush $palette[3]
            $g.FillRectangle($brush, $x * $CELL, $y * $CELL, $CELL, $CELL)
            $brush.Dispose()
        }
    }
    $g.Dispose()
    $out = Join-Path $OutDir ("flame_{0}.png" -f $l.Name)
    $bmp.Save($out, [System.Drawing.Imaging.ImageFormat]::Png)
    $bmp.Dispose()
    Write-Output "$out  ($($cols * $CELL)x$($rows * $CELL))"
}
