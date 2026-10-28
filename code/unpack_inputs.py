"""Extract all released Beijing archives; preserve the original nested input layout."""
from pathlib import Path
from zipfile import ZipFile
import argparse
def main():
    parser=argparse.ArgumentParser();parser.add_argument("--figure5",action="store_true")
    args=parser.parse_args();root=Path(__file__).resolve().parent
    if args.figure5:pairs=[(root.parent/"figure/figure5/b",root.parent/"figure/figure5/b/hourly_inputs")]
    else:pairs=[(root/"data/energyplus_outputs"/c,root/"data/energyplus_outputs"/c) for c in ["IndoorEnv","Energy","Capacity"]]
    for src,dst in pairs:
        for archive in sorted(src.glob("*.zip")):
            with ZipFile(archive) as z:
                for member in z.infolist():
                    target=(dst/member.filename).resolve()
                    if not target.is_relative_to(dst.resolve()):raise ValueError("Invalid archive path")
                z.extractall(dst)
                print(archive.name,len(z.infolist()),"files")
if __name__=="__main__":main()
