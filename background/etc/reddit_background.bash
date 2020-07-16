#/usr/bin/env bash

__reddit_background(){
    local subcommand

    COMPREPLY=()
    subcommand=${COMP_WORDS[COMP_CWORD]}
    
    case "$subcommand" in
        -* | --)
            COMPREPLY=( $(compgen -W '--background-setting --image-count --desktop --what -v --version -h --help' -- $subcommand) ) ;;
    esac
    return 0
}

complete -F __reddit_background reddit_background 
