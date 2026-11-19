"""Procedural fastener meshes (.tri/.edg) in local frame: screw head top at z=0, shaft along -z."""
import numpy as np, os
import os
OUT=os.path.join(os.path.dirname(os.path.abspath(__file__)),'data'); os.makedirs(OUT,exist_ok=True)
def cyl(r, z0, z1, n=24, cap0=True, cap1=True):
    a=np.linspace(0,2*np.pi,n,endpoint=False); c=np.c_[np.cos(a),np.sin(a)]
    T=[];E=[]
    for i in range(n):
        j=(i+1)%n
        p00=[r*c[i,0],r*c[i,1],z0];p01=[r*c[j,0],r*c[j,1],z0];p10=[r*c[i,0],r*c[i,1],z1];p11=[r*c[j,0],r*c[j,1],z1]
        T+= [[p00,p01,p11],[p00,p11,p10]]
        if cap0: T.append([[0,0,z0],p01,p00])
        if cap1: T.append([[0,0,z1],p10,p11])
        E+= [[p00,p01],[p10,p11]]
    return T,E
def hexprism(af, z0, z1, hole=None):
    rc=af/2/np.cos(np.pi/6); a=np.arange(6)*np.pi/3+np.pi/6; c=np.c_[np.cos(a),np.sin(a)]*rc
    T=[];E=[]
    for i in range(6):
        j=(i+1)%6; p00=[*c[i],z0];p01=[*c[j],z0];p10=[*c[i],z1];p11=[*c[j],z1]
        T+=[[p00,p01,p11],[p00,p11,p10],[[0,0,z0],p01,p00],[[0,0,z1],p10,p11]]
        E+=[[p00,p01],[p10,p11],[p00,p10]]
    return T,E
def save(name,parts):
    T=[];E=[]
    for t,e in parts: T+=t;E+=e
    T=np.array(T,float);E=np.array(E,float)
    n=np.cross(T[:,1]-T[:,0],T[:,2]-T[:,0]); n/=np.maximum(np.linalg.norm(n,axis=1,keepdims=True),1e-9)
    np.concatenate([T.reshape(-1,3),np.repeat(n,3,axis=0)],1).astype('<f4').tofile(f'{OUT}/{name}.tri')
    E.reshape(-1,3).astype('<f4').tofile(f'{OUT}/{name}.edg')
def cone(r0,z0,r1,z1,n=24):
    a=np.linspace(0,2*np.pi,n,endpoint=False); c=np.c_[np.cos(a),np.sin(a)]; T=[];E=[]
    for i in range(n):
        j=(i+1)%n; p00=[r0*c[i,0],r0*c[i,1],z0];p01=[r0*c[j,0],r0*c[j,1],z0];p10=[r1*c[i,0],r1*c[i,1],z1];p11=[r1*c[j,0],r1*c[j,1],z1]
        T+=[[p00,p01,p11],[p00,p11,p10],[[0,0,z1],p10,p11]]; E+=[[p10,p11]]
    return T,E
def csk(d,L,dk,name):
    # ISO 10642 countersunk: head top flush at z=0, 90 deg cone down to the shank; L includes the head
    h=(dk-d)/2
    save(name,[cone(d/2*0.95,-h,dk/2,0),cyl(d/2*0.95,-L,-h,16,cap1=False)])
def shcs(d,L,dk,k,name):
    save(name,[cyl(dk/2,0,k),cyl(d/2*0.95,-L,0,16)])   # head z in [0,k]; shaft below
# The CAD models the M2.5 standoffs and the screws that hold the camera and the
# standoff stack. These are the two sets it leaves out.
shcs(2.5,5,4.5,2.5,'scr_M2.5x5')      # LCD HAT -> standoffs, heads sit in the lid's 4.5 mm openings
shcs(2,8,3.8,1.3,'pan_M2x8')          # lid -> corner posts (pan / self-tapping head)
print('ok')

# 220x220 bed plate used for print previews
s=110; z=-0.3
v=np.array([[-s,-s,z],[s,-s,z],[s,s,z],[-s,-s,z],[s,s,z],[-s,s,z]],dtype='<f4'); n=np.tile([0,0,1],(6,1)).astype('<f4')
np.concatenate([v,n],1).tofile(f'{OUT}/bedplate.tri')
L=[]
for x in range(-110,111,20): L+=[[x,-s,z+0.05],[x,s,z+0.05],[-s,x,z+0.05],[s,x,z+0.05]]
np.array(L,dtype='<f4').tofile(f'{OUT}/bedplate.edg')
