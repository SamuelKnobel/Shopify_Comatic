$file = "Comatic API.txt"
$patterns = @("shipping", "discount", "VatType", "VatCode", "ArticleId", "TotalAmount")
foreach ($p in $patterns) {
    Write-Output "--- Search Pattern: $p ---"
    Select-String -Path $file -Pattern $p | Select-Object -First 20 | ForEach-Object {
        $len = [System.Math]::Min(300, $_.Line.Length)
        Write-Output ("L" + $_.LineNumber + ": " + $_.Line.Substring(0, $len))
    }
}
