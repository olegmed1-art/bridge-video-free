#include <iostream>
#include <cstring>
#include "dds.h"
int main(int argc,char**argv){if(argc!=2)return 64; SetMaxThreads(0); ddTableDealPBN d{}; ddTableResults r{}; std::strncpy(d.cards,argv[1],sizeof(d.cards)-1); int rc=CalcDDtablePBN(d,&r); if(rc!=RETURN_NO_FAULT){char line[80]; ErrorMessage(rc,line); std::cerr<<line<<"\n"; return rc;} const char* s[5]={"S","H","D","C","NT"}; std::cout<<"{\"dd_table\":{"; for(int i=0;i<5;i++){if(i)std::cout<<","; std::cout<<"\""<<s[i]<<"\":["; for(int p=0;p<4;p++){if(p)std::cout<<",";std::cout<<r.resTable[i][p];} std::cout<<"]";} std::cout<<"}}\n"; }
