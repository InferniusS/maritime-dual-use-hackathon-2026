"""Optional isolated OpenCascade worker. Export shapes use meter coordinates and write.step.unit M; reader inspection converts default kernel mm back to m."""
import json,sys,math
from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder,BRepPrimAPI_MakeBox
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCP.gp import gp_Ax2,gp_Pnt,gp_Dir
from OCP.BRep import BRep_Builder
from OCP.TopoDS import TopoDS_Compound
from OCP.STEPCAFControl import STEPCAFControl_Writer
from OCP.STEPControl import STEPControl_AsIs,STEPControl_Reader
from OCP.IFSelect import IFSelect_RetDone
from OCP.Interface import Interface_Static
from OCP.TDocStd import TDocStd_Document
from OCP.TCollection import TCollection_ExtendedString
from OCP.XCAFDoc import XCAFDoc_DocumentTool
from OCP.TDataStd import TDataStd_Name
from OCP.Bnd import Bnd_Box
from OCP.BRepBndLib import BRepBndLib
from OCP.GProp import GProp_GProps
from OCP.BRepGProp import BRepGProp
from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_SOLID

path=sys.argv[1]
if len(sys.argv)>2 and sys.argv[2]=='inspect':
    reader=STEPControl_Reader()
    if reader.ReadFile(path)!=IFSelect_RetDone:raise ValueError('STEP read failed')
    reader.TransferRoots();shape=reader.OneShape();box=Bnd_Box();BRepBndLib.Add_s(shape,box)
    props=GProp_GProps();BRepGProp.VolumeProperties_s(shape,props)
    explorer=TopExp_Explorer(shape,TopAbs_SOLID);count=0
    while explorer.More():count+=1;explorer.Next()
    names=[]
    # Names are preserved as STEP PRODUCT labels; export accompanying JSON carries semantics.
    print(json.dumps({'bounds_m':[x/1000 for x in box.Get()],'volume_m3':props.Mass()/1e9,'solids':count}))
else:
    model=json.load(sys.stdin)
    doc=TDocStd_Document(TCollection_ExtendedString('BinXCAF'));tool=XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    for pipe in model['pipes']:
        compound=TopoDS_Compound();builder=BRep_Builder();builder.MakeCompound(compound)
        for i,(a,b) in enumerate(zip(pipe['points'],pipe['points'][1:])):
            delta=[(b[j]-a[j]) for j in range(3)];length=math.sqrt(sum(v*v for v in delta))
            ax=gp_Ax2(gp_Pnt(*(v for v in a)),gp_Dir(*delta))
            outer=BRepPrimAPI_MakeCylinder(ax,pipe['outer_diameter']/2,length).Shape()
            if pipe.get('inner_diameter'):
                inner=BRepPrimAPI_MakeCylinder(ax,pipe['inner_diameter']/2,length).Shape()
                outer=BRepAlgoAPI_Cut(outer,inner).Shape()
            builder.Add(compound,outer)
        label=tool.AddShape(compound,False);TDataStd_Name.Set_s(label,TCollection_ExtendedString('PIPE_'+pipe['id']))
    for obstacle in model['obstacles']:
        lo=obstacle['min'];hi=obstacle['max']
        box=BRepPrimAPI_MakeBox(gp_Pnt(*(v for v in lo)),*((hi[j]-lo[j]) for j in range(3))).Shape()
        label=tool.AddShape(box,False);TDataStd_Name.Set_s(label,TCollection_ExtendedString('ENV_'+obstacle['id']))
    writer=STEPCAFControl_Writer();writer.SetNameMode(True)
    Interface_Static.SetCVal_s('write.step.unit','M')
    if not writer.Transfer(doc,STEPControl_AsIs):raise ValueError('STEP transfer failed')
    if writer.Write(path)!=IFSelect_RetDone:raise ValueError('STEP write failed')
