# -*- coding: utf-8 -*-
"""
Created on Thu Dec 08 01:44:04 2016

@author: Ben
"""

# OK. Remember to commit changes before changing branches.
# The branches will work separately if this is done.
# all branches see uncommitted changes unless they are based on different commits


import subprocess

import pdb
import numpy as np
import time
import sys, os
from functions import *
from mpi4py import MPI
import psutil

process = psutil.Process(os.getpid())

comm = MPI.COMM_WORLD
nprocs = comm.size
myrank = comm.rank

if myrank==0:
    time0 = time.time()

def bash_command(cmd):
    subprocess.Popen(['/bin/bash', '-c', cmd])

def mymkdir(mydir):
    if not os.path.exists(mydir):
        print 'making ',mydir
        os.mkdir(mydir)

inputfile = sys.argv[1]
savedir   = sys.argv[2]

if myrank==0:
    print ' '
    print 'nprocs = ',nprocs
    
    mymkdir(savedir)
    mymkdir(savedir+'Glocdir/')
    mymkdir(savedir+'Sdir/')

comm.barrier()
        
with open(inputfile,'r') as f:
    Nt    = int(parseline(f.readline()))
    Ntau  = int(parseline(f.readline()))
    dt    = float(parseline(f.readline()))
    dtau  = float(parseline(f.readline()))
    Nkx   = int(parseline(f.readline()))
    Nky   = int(parseline(f.readline()))
    g2    = float(parseline(f.readline()))
    omega = float(parseline(f.readline()))    
    pump  = int(parseline(f.readline()))
    
if myrank==0:
    print '\n','Params'
    print 'Nt    = ',Nt
    print 'Ntau  = ',Ntau
    print 'dt    = ',dt
    print 'dtau  = ',dtau
    print 'Nkx   = ',Nkx
    print 'Nky   = ',Nky
    print 'g2    = ',g2
    print 'omega = ',omega
    print 'pump  = ',pump
    print '\n'

    
Norbs = 2

if myrank==0:
    startTime = time.time()

    
## k2p is k indices to processor number
k2p, k2i, i2k = init_k2p_k2i_i2k(Nkx, Nky, nprocs, myrank)

# kpp is the number of k points on this process
kpp = np.count_nonzero(k2p==myrank)

if myrank==0:
    print "kpp =",kpp

UksR, UksI, eks, fks = init_Uks(myrank, Nkx, Nky, kpp, k2p, k2i, Nt, Ntau, dt, dtau, pump, Norbs)


if myrank==0:
    print "done with Uks initialization"


comm.barrier()        
if myrank==0:
    print "Initialization time ", time.time()-startTime,'\n'


#volume = Nkx*Nky * np.sqrt(3.)/3. # the real space volume
#volume = Nkx*Nky * 8*np.pi**2/(3.*np.sqrt(3.)) # the k space volume
volume = Nkx*Nky


########## ---------------- Compute the electron selfenergy due to phonons -------------------- ##############

if myrank==0:
    timeStart = time.time()

D = init_D(omega, Nt, Ntau, dt, dtau, Norbs)

def myprint(x):
    print np.amax(np.abs(x.L))
    print np.amax(np.abs(x.G))
    print np.amax(np.abs(x.IR))
    print np.amax(np.abs(x.RI))
    print np.amax(np.abs(x.M))
    

if myrank==0:
    print 'max D'
    print D

Gloc_proc = langreth(Nt, Ntau, Norbs)
temp = langreth(Nt, Ntau, Norbs)
Sigma_phonon = langreth(Nt, Ntau, Norbs)

# compute local Greens function for each processor
for ik in range(kpp):
    ik1,ik2 = i2k[ik]

    G0k = compute_G0(ik1, ik2, myrank, Nkx, Nky, kpp, k2p, k2i, Nt, Ntau, dt, dtau, fks, UksR, UksI, eks, Norbs)
    Gloc_proc.add(G0k)

if myrank==0:
    print "Initialization of D and G0k time ", time.time()-timeStart,'\n'

iter_selfconsistency = 3
for myiter in range(iter_selfconsistency):
        
    if myrank==0:
        timeStart = time.time()

        print 'max Gloc_proc'
        print Gloc_proc

    Sigma_phonon.zero(Nt, Ntau, Norbs)

    # store complete local Greens function in Sigma_phonon
    comm.Allreduce(Gloc_proc.L,  Sigma_phonon.L,  op=MPI.SUM)
    comm.Allreduce(Gloc_proc.G,  Sigma_phonon.G,  op=MPI.SUM)
    comm.Allreduce(Gloc_proc.IR, Sigma_phonon.IR, op=MPI.SUM)
    comm.Allreduce(Gloc_proc.RI, Sigma_phonon.RI, op=MPI.SUM)
    comm.Allreduce(Gloc_proc.M,  Sigma_phonon.M,  op=MPI.SUM)

    # save DOS (stored in Sigma_phonon currently)
    if myrank==0:
        Sigma_phonon.mysave(savedir+'Glocdir/Gloc')

    comm.barrier()
    Sigma_phonon.directMultiply(D)
    Sigma_phonon.scale(1j * g2 / volume)

    if myrank==0:
        print 'max Sigma_phonon'
        print Sigma_phonon
    
    #pdb.set_trace()
    #print "after sigma phonon"
    
    if myrank==0:
        print "iteration",myiter
        print "time computing phonon selfenergy ", time.time()-timeStart,'\n'
        timeStart = time.time()

    # unnecessary to compute DOS on the last iteration
    if myiter<iter_selfconsistency-1:

        Gloc_proc.zero(Nt, Ntau, Norbs)

        #now compute G
        if myrank==0:
            timeStart = time.time()
        for ik in range(kpp):
            temp.zero(Nt, Ntau, Norbs)

            ik1, ik2 = i2k[ik]
            G0k = compute_G0(ik1, ik2, myrank, Nkx, Nky, kpp, k2p, k2i, Nt, Ntau, dt, dtau, fks, UksR, UksI, eks, Norbs)

            temp = multiply(G0k, Sigma_phonon, Nt, Ntau, dt, dtau, Norbs)

            temp.scale(-1.0)

            # I think the integral done by multiply means temp has no delta piece
            # we add a delta piece to add the identity in I - G0*Sigma
            temp.DR = np.ones(Norbs*Nt) / dt
            temp.DM = np.ones(Norbs*Ntau) / (-1j*dtau)

            # copies are good so that the diagonals of the langreth matrices don't get changed during solve
            #temp2 = langreth(Nt, Ntau, Norbs)
            #temp2.mycopy(G0k)
            #out = solve(temp, G0k, Nt, Ntau, dt, dtau, Norbs)

            temp = solve(temp, G0k, Nt, Ntau, dt, dtau, Norbs)

            Gloc_proc.add(temp)


    comm.barrier()       
    if myrank==0:
        print "Done iteration ", time.time()-timeStart,'\n'

# save the selfenergy
if myrank==0:
    Sigma_phonon.mysave(savedir+'Sdir/S')
    
comm.barrier()        
if myrank==0:
    print 'finished program'
    print 'total time ',time.time()-time0

MPI.Finalize()
    

    
