from pathlib import Path

from PIL import Image, ImageChops, ImageStat


def compare(actual, baseline, diff_path, tolerance=12, max_changed_fraction=0.01, region=None):
    """Compare approved, fixed-camera references. No baseline means no pass."""
    if not Path(baseline).is_file():
        raise ValueError("Approved reference image is missing; cannot establish visual correctness")
    if not 0 <= tolerance <= 255 or not 0 <= max_changed_fraction <= 1:
        raise ValueError("Invalid comparison thresholds")
    with Image.open(actual) as a, Image.open(baseline) as b:
        a, b = a.convert("RGB"), b.convert("RGB")
        if a.size != b.size:
            return {
                "status": "blocked",
                "reason": "Resolution mismatch",
                "actual": a.size,
                "baseline": b.size,
            }
        if region is not None:
            if (
                len(region) != 4
                or any(not 0 <= v <= 1 for v in region)
                or region[0] >= region[2]
                or region[1] >= region[3]
            ):
                raise ValueError("Region must be [left, top, right, bottom] normalized to 0–1")
            box = tuple(round(v * a.size[i % 2]) for i, v in enumerate(region))
            if box[0] == box[2] or box[1] == box[3]:
                raise ValueError("Comparison region is smaller than one pixel")
            a, b = a.crop(box), b.crop(box)
        diff = ImageChops.difference(a, b)
        diff.save(diff_path)
        channels = diff.split()
        maximum = ImageChops.lighter(ImageChops.lighter(channels[0], channels[1]), channels[2])
        histogram = maximum.histogram()
        changed = sum(histogram[int(tolerance) + 1 :])
        fraction = changed / (a.width * a.height)
        return {
            "status": "passed" if fraction <= max_changed_fraction else "failed",
            "changed_fraction": fraction,
            "mean_channel_difference": sum(ImageStat.Stat(diff).mean) / 3,
            "pixel_tolerance": tolerance,
            "allowed_changed_fraction": max_changed_fraction,
            "region": region,
            "diff": str(diff_path),
            "scope": "Pixel similarity to a supplied reference; not proof of artistic correctness",
        }
