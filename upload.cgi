#!/usr/bin/perl
### SibSoft.net ###
use strict;
$ENV{PERL_LWP_SSL_VERIFY_HOSTNAME}=0;
use lib '.';
use XFSConfig;
use XUpload;
use CGI::Carp qw(fatalsToBrowser);
use CGI;
use LWP::UserAgent;
use HTTP::Cookies;
use HTML::Form;
use HTTP::Request::Common;
use File::Temp;
use URI;
use JSON;
use Log;

Log->new(filename => 'upload.log');
$|++;

print("Access-Control-Allow-Origin: *\n"); # Allow Cross-domain XHRs
print("Content-type: text/plain\n\nOK"), exit if $ENV{REQUEST_METHOD} eq 'OPTIONS';

my $temp_dir = File::Temp::tempdir(DIR => $c->{temp_dir}, CLEANUP => 1);
$CGITempFile::TMPDIRECTORY = $temp_dir;

my $cg = CGI->new();
my $f;
$f->{$_}=$cg->param($_) for $cg->param();

print("Content-type:text/html\n\nXFS"),exit if $ENV{QUERY_STRING}=~/mode=test/;

&kill_session() if $f->{kill};

my $utype = $cg->param('utype');
$utype||='anon';
$c->{$_}=$c->{"$_\_$utype"} for qw(enabled max_upload_files max_upload_filesize max_rs_leech remote_url leech);

my ($upload_type) = $ENV{QUERY_STRING}=~/upload_type=([a-z]+)/;

print STDERR "Starting upload. Size: $ENV{'CONTENT_LENGTH'} Upload type: $upload_type\n";
my ($sid) = ($ENV{QUERY_STRING}=~/upload_id=(\d+)/); # get the random id for temp files

my @urls;

$c->{ip_not_allowed}=~s/\./\\./g;
if( $c->{ip_not_allowed} && &GetIP() =~ /$c->{ip_not_allowed}/ ) {
    &xmessage("ERROR: $c->{msg}->{ip_not_allowed}");
}
if(!$c->{enabled}) {
    &xmessage("ERROR: Uploads not enabled for this type of users");
}
if($c->{srv_status} ne 'ON') {
    &xmessage("ERROR: Server don't allow uploads at the moment");
}
if($c->{max_upload_filesize} && $ENV{CONTENT_LENGTH} > 1048576*$c->{max_upload_filesize}*$c->{max_upload_files}) {
    &xmessage("ERROR: $c->{msg}->{file_size_big}$c->{max_upload_filesize} Mb");
}

print "Content-type: application/json\n\n";

&TorrentUpload() if $f->{torr_on};
my @file_inputs = $upload_type eq 'url' ? &URLUpload() : &FileUpload();
my @results = ProcessFiles(@file_inputs);
print JSON::encode_json(\@results);
exit();

sub ProcessFiles
{
    my @file_inputs = @_;
    my @files;

    $f->{ip} = &GetIP();
    $f->{torr_on}=1 if $f->{up1oad_type} eq 'tt';

    for my $file ( @file_inputs ) {
        $file->{file_status}="null filesize or wrong file path"
            if $file->{file_size}==0;

        $file->{file_status}="filesize too big"
            if $c->{max_upload_filesize} && $file->{file_size}>$c->{max_upload_filesize}*1048576;

        $file->{file_status}="too many files"
            if $#files>=$c->{max_upload_files};

        $file->{file_status}=$file->{file_error}
        if $file->{file_error};

# --------------------
        $file = &XUpload::ProcessFile($file,$f) unless $file->{file_status};
# --------------------

        $file->{file_status}||='OK';
        push @files, $file;
    }

    my @results = map { { file_code => $_->{file_code} || 'undef', file_status => $_->{file_status} } } @files;
    return @results;
}

sub leech_traffic_left
{
    my $ua2 = LWP::UserAgent->new(agent => "XFS-FSAgent", timeout => 90);
    my $res = $ua2->post("$c->{site_cgi}/fs.cgi", {
            op           => 'leech_mb_left',
            fs_key       => $c->{fs_key},
            sess_id      => $cg->param('sess_id')||'',
            file_ip      => $ENV{REMOTE_ADDR},
            })->content;
    return $1 if $res=~/^OK:(\d+)$/;
}

sub register_leeched_traffic
{
    my $ua2 = LWP::UserAgent->new(agent => "XFS-FSAgent", timeout => 90);
    my $res = $ua2->post("$c->{site_cgi}/fs.cgi", {
            op           => 'register_leeched',
            fs_key       => $c->{fs_key},
            sess_id      => $cg->param('sess_id')||'',
            size         => $_[0],
            ip           => $ENV{REMOTE_ADDR},
            })->content;
}

sub kill_session
{
    my $sid = $1 if $f->{kill} =~ /^(\d+)$/;
    &Send("No sid") if !$sid;

    my $session_data = "$c->{htdocs_tmp_dir}/$sid.json";
    open (FILE, $session_data) || &Send("No session data");
    my $json = JSON::decode_json(<FILE>);
    close FILE;

    kill 9, $json->{pid};
    unlink($session_data);
    &Send("OK");
}

sub URLUpload
{
    require SessionF;
    require Plugin;

    my $ua = LWP::UserAgent->new(timeout => 90,
            requests_redirectable => ['GET', 'HEAD','POST'],
            agent   => 'Mozilla/5.0 (Windows; U; Windows NT 5.1; ru; rv:1.9.1.6) Gecko/20091201 Firefox/3.5.6 (.NET CLR 3.5.30729)',
            cookie_jar => HTTP::Cookies->new( hide_cookie2 => 1, ignore_discard => 1 ) );

    $Plugin::browser = $ua;

    my $ses = SessionF->new();
    $ses->LoadPlugins();

    my $logged_in = {};

    my $external_keys = {};
    for(split(/\|/, $c->{external_keys}))
    {
        my ($domain, $key) = split(/:/, $_, 2);
        $external_keys->{$domain} = $key;
    }

    sub randomize
    {
        my @lines = split(/\n\r?/, $_[0]);
        return $lines[int rand(@lines)];
    }

    sub computeName
    {
        # Figure out the resulting file_name in the same way as the browsers do
        my ($resp) = @_;
        my $content_disp = $resp->header('Content-Disposition');
        return $1 if $content_disp =~ /filename="(.+)"/
                    || $content_disp =~ /filename=(.+)/
                    || $content_disp =~ /filename\*=.*?''(.+)/;
        return $1 if URI->new($resp->request->uri)->path =~ /([^\/]+)$/;
    }

    sub Leech
    {
        my ($plugin, $url) = @_;
        $Plugin::tmpfile = "$c->{temp_dir}/".join('', map int rand(10), 1..10);

        my $uri = URI->new($url);
        my $userinfo = $uri->userinfo();
        $uri->userinfo('');

        my $prefix = $plugin->options->{plugin_prefix};
        my $can_login = $plugin->options->{plugin_prefix};
        my ($login, $pass) = split(':', $userinfo || $f->{"$prefix\_logins"} || &randomize($c->{"$prefix\_logins"}), 2);
        $plugin->login({ login => $login, password => $pass });

        my $ret = $plugin->download($uri->as_string, "", make_lwp_hook($sid));
        return { file_tmp => $Plugin::tmpfile, file_name_orig => $ret->{filename}, file_size => $ret->{filesize} };
    }

    sub APILeech
    {
        my ($domain, $url) = @_;
        my $file_code = $1 if $url =~ s/\/(\w{12}).*//;
        return { file_error => "File not found" } if !$file_code;

        my $res = $ua->post($url,
        {
            op => 'external',
            download => 1,
            api_key => $external_keys->{$domain},
            file_code => $file_code,
        });
        my $ret = JSON::decode_json($res->decoded_content);

        return &DirectDownload($ret->{direct_link});
    }

    sub DirectDownload
    {
        my ($url) = @_;
        return if $url !~ /^(http|https|ftp):\/\//;
        my $tempfile = "$c->{temp_dir}/".join('', map int rand(10), 1..10);
        open FILE, ">$tempfile"|| die"Can't open dest file:$!";
        my $req = HTTP::Request->new(GET => $url);
        my $resp = $ua->request($req, make_lwp_hook($sid));
        close FILE;

        if($c->{max_rs_leech})
        {
	        return { file_error => 'No leech traffic left' } if leech_traffic_left() < $resp->content_length;
	        register_leeched_traffic($resp->content_length);
        }

        return { file_tmp => $tempfile, file_name_orig => &computeName($resp), file_size => -s $tempfile };
    }

    my @file_inputs;
    for my $url(split(/\n\r?/, $f->{url_mass}))
    {
        my $domain = $1 if $url =~ /^https?:\/\/([^\/:]+)/; 
        my ($plugin) = grep { $_->check_link($url) } @{ $ses->getPlugins() };
        my $input;

        if($plugin && $c->{leech})
        {
	        push @file_inputs, &Leech($plugin, $url);
        }
        elsif($external_keys->{$domain} && $c->{leech})
        {
            push @file_inputs, &APILeech($domain, $url);
        }
        elsif($c->{remote_url})
        {
	        push @file_inputs, &DirectDownload($url);
        }
        else
        {
            push @file_inputs, { file_status => "Not allowed" };
        }
    }

    return @file_inputs;
}

sub make_lwp_hook
{
    my ($sid) = @_;
    my ($total_size, $current_bytes, $old_time, $base_old, $fname2);
    my $files_uploaded = 0;

    # hook_url
	sub hook_url
    {
	    my ($buffer,$res) = @_;
	    print FILE $buffer;
	    $current_bytes+=length($buffer);
	
	    if(time>$old_time)
        {
	        $total_size += $res->content_length if $base_old ne $res->base;
	        $base_old = $res->base;
	        $fname2 = $res->base;
	        $old_time = time;

	        print "# Keep-Alive\n" if $f->{keepalive};

	        open(F,">$c->{htdocs_tmp_dir}/$sid.json");
	        print F JSON::encode_json(
	            {
	                pid => $$,
	                state => "uploading",
	                total => "$total_size",
	                loaded => "$current_bytes",
	                files_done => "$files_uploaded"
	            });
	        close F;
	    }
	};

    return \&hook_url;
}

sub FileUpload
{
    my @file_inputs;

    for my $input( $cg->param() ) {
        my @params = $cg->param($input);
        next unless my @file_names=$cg->upload($input);
        # HTML5 multi-upload support
        my $i = 0;
        foreach(@file_names) {
            my $u;
            ($u->{file_name_orig})=$cg->uploadInfo($_)->{'Content-Disposition'}=~/filename="(.+?)"/i;
            $u->{file_name_orig}=~s/^.*\\([^\\]*)$/$1/;
            $u->{file_size}   = -s $_;
            $u->{file_descr}  = ($cg->param("file_descr"))[$i];
            $u->{file_public} = ($cg->param("file_public"))[$i];
            print STDERR "input=$input public=$u->{file_public}\n";
            $u->{file_adult}  = ($cg->param("file_adult"))[$i];
            $u->{file_tmp}    = $cg->tmpFileName($_);
            push @file_inputs, $u;
            $i++;
        }
    }

    return @file_inputs;
}

sub parseTorrent
{
    require BitTorrent;
    my $bt = BitTorrent->new();
    my $tt = $bt->getTrackerInfo($_[0]);

    my ($over,$files);
    foreach my $ff ( @{$tt->{files}} )
    {
        next if $ff->{name}=~/padding_file/;
        $over=1 if $ff->{size} > $c->{max_upload_filesize}*1048576;
        $files.="$ff->{name}:$ff->{size}\n";
    }

    &Send("One or more files in torrent exceed filesize limit of $c->{max_upload_filesize} Mb") if $c->{max_upload_filesize} && $over;
    return $tt->{hash};
}

sub TorrentUpload
{
    require TorrentClient;

    my $ua = LWP::UserAgent->new('XFS-FSAgent');
    my $tt = TorrentClient->new();

    my $hash;
    $hash = parseTorrent($cg->tmpFileName($f->{file_0})) if $f->{file_0};
    $hash = lc($1) if $f->{magnet} =~ /btih:([0-9a-zA-Z]+)/;

    my $res = $ua->post("$c->{site_cgi}/fs.cgi", {
        op => 'add_torrent',
        sid => $hash,
        sess_id => $f->{sess_id},
        fs_key => $c->{fs_key},
        fld_id => $f->{fld_id}||0,
        link_rcpt => $f->{link_rcpt}||'',
        link_pass => $f->{link_pass}||'',
    });

    &Send("<b>Error while adding torrent:</b><br>" . $res->decoded_content) if $res->decoded_content !~ /^OK/i;

    if($f->{file_0})
    {
	    my $file_tmp = $cg->tmpFileName($f->{file_0});
	    $res = $tt->startdl(metainfo => [ $file_tmp ], fs_key => $c->{fs_key});
    }

    if($f->{magnet})
    {
        $res = $tt->startdl(magnet => $f->{magnet}, fs_key => $c->{fs_key});
    }

    if($res =~ /^OK/)
    {
        print "Location: $c->{site_url}/?op=my_files\n";
        print "Status: 302\n";
        &XUpload::Send("OK");
    }
}

#########################

unlink("$c->{htdocs_tmp_dir}/$sid.html");

#############################################

sub xmessage {
    print "Status: 500\n";
    print "Content-type: text/plain\n\n$_[0]";
    exit();
}

sub GetIP {
    return $ENV{REMOTE_ADDR};
}