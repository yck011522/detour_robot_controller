Add-Type -AssemblyName System.Drawing
$ErrorActionPreference = 'Stop'
$assetDirectory = Join-Path (Split-Path -Parent $PSScriptRoot) 'asset/ui_layout'
$scale = 3
foreach ($file in Get-ChildItem -LiteralPath $assetDirectory -Filter 'split_*_static.svg') {
    [xml]$doc = Get-Content -LiteralPath $file.FullName -Encoding UTF8
    $width = [int]$doc.DocumentElement.width
    $height = [int]$doc.DocumentElement.height
    $bmp = [System.Drawing.Bitmap]::new($width*$scale,$height*$scale)
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.Clear([System.Drawing.Color]::Transparent)
    $g.ScaleTransform($scale,$scale)
    $g.SmoothingMode = 'AntiAlias'
    $g.TextRenderingHint = 'AntiAliasGridFit'
    foreach ($el in $doc.DocumentElement.ChildNodes) {
        if ($el.LocalName -eq 'rect') {
            $alpha = if ($el.HasAttribute('opacity')) { [int](255*[double]$el.opacity) } else {255}
            $color = [System.Drawing.ColorTranslator]::FromHtml($el.fill)
            $brush = [System.Drawing.SolidBrush]::new([System.Drawing.Color]::FromArgb($alpha,$color))
            $path = [System.Drawing.Drawing2D.GraphicsPath]::new()
            $r = 2*[single]$el.rx
            $w = [single]$el.width; $h = [single]$el.height
            $path.AddArc(0,0,$r,$r,180,90)
            $path.AddArc(($w-$r),0,$r,$r,270,90)
            $path.AddArc(($w-$r),($h-$r),$r,$r,0,90)
            $path.AddArc(0,($h-$r),$r,$r,90,90)
            $path.CloseFigure()
            $g.FillPath($brush,$path)
            $path.Dispose(); $brush.Dispose()
        } elseif ($el.LocalName -eq 'polyline') {
            $points = [System.Drawing.PointF[]]@(($el.points -split ' ') | Where-Object {$_} | ForEach-Object {
                $xy = $_ -split ','
                [System.Drawing.PointF]::new([single]$xy[0],[single]$xy[1])
            })
            $pen = [System.Drawing.Pen]::new([System.Drawing.ColorTranslator]::FromHtml($el.stroke),[single]$el.'stroke-width')
            $g.DrawLines($pen,$points)
            $pen.Dispose()
        } elseif ($el.LocalName -eq 'text') {
            $style = if ($el.'font-weight' -in @('600','700')) {'Bold'} else {'Regular'}
            $font = [System.Drawing.Font]::new([string]$el.'font-family',[single]$el.'font-size',[System.Drawing.FontStyle]::$style,[System.Drawing.GraphicsUnit]::Pixel)
            $brush = [System.Drawing.SolidBrush]::new([System.Drawing.ColorTranslator]::FromHtml($el.fill))
            $format = [System.Drawing.StringFormat]::GenericTypographic.Clone()
            if ($el.'text-anchor' -eq 'middle') {$format.Alignment = 'Center'}
            $g.DrawString($el.InnerText,$font,$brush,[System.Drawing.PointF]::new([single]$el.x,[single]$el.y),$format)
            $format.Dispose(); $font.Dispose(); $brush.Dispose()
        }
    }
    $g.Dispose()
    $small = [System.Drawing.Bitmap]::new($width,$height)
    $sg = [System.Drawing.Graphics]::FromImage($small)
    $sg.InterpolationMode = 'HighQualityBicubic'
    $sg.DrawImage($bmp,0,0,$width,$height)
    $small.Save([System.IO.Path]::ChangeExtension($file.FullName,'.png'),[System.Drawing.Imaging.ImageFormat]::Png)
    $sg.Dispose(); $small.Dispose(); $bmp.Dispose()
}
